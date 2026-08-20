import httpx
import pytest

from model_prediction.data_sources.balldontlie import BallDontLieClient


def test_requires_api_key() -> None:
    with pytest.raises(ValueError, match="BALLDONTLIE_API_KEY"):
        BallDontLieClient("")


def test_mlb_player_injuries_sends_auth_header_and_filters() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/mlb/v1/player_injuries"
        assert request.headers["authorization"] == "test-key"
        assert request.url.params["team_ids[]"] == "108"
        return httpx.Response(
            200,
            json={"data": [{"player_id": 1, "status": "day-to-day"}], "meta": {"next_cursor": None}},
        )

    client = BallDontLieClient("test-key", client=httpx.Client(transport=httpx.MockTransport(handler)))
    injuries = client.mlb_player_injuries(team_ids=[108])
    assert injuries == [{"player_id": 1, "status": "day-to-day"}]


def test_pagination_walks_next_cursor_to_exhaustion() -> None:
    pages = [
        {"data": [{"id": 1}], "meta": {"next_cursor": 2}},
        {"data": [{"id": 2}], "meta": {"next_cursor": None}},
    ]
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.params.get("cursor"))
        return httpx.Response(200, json=pages[len(calls) - 1])

    client = BallDontLieClient("test-key", client=httpx.Client(transport=httpx.MockTransport(handler)))
    rows = client.mlb_plate_appearances(game_ids=[555])
    assert rows == [{"id": 1}, {"id": 2}]
    assert calls == [None, "2"]


def test_http_status_error_is_normalized_to_http_error_without_crashing() -> None:
    """The key is sent via header, not a URL query param, so it never lands
    in httpx's error message -- but the redaction/reconstruction path
    (the_odds_api.py's _safe_get pattern) must still not itself raise a
    TypeError trying to rebuild HTTPStatusError from a message-only arg."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="Unauthorized")

    client = BallDontLieClient("secret-abc-123", client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(httpx.HTTPError) as excinfo:
        client.mlb_player_injuries()
    assert "secret-abc-123" not in str(excinfo.value)
