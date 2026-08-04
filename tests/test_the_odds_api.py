import httpx
import pytest

from model_prediction.data_sources.the_odds_api import TheOddsAPIClient


def test_active_tennis_sports_and_tournament_odds_use_discovered_keys() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v4/sports":
            return httpx.Response(
                200,
                json=[
                    {"key": "baseball_mlb", "group": "Baseball", "active": True},
                    {"key": "tennis_atp_us_open", "group": "Tennis", "active": True},
                    {"key": "tennis_wta_old", "group": "Tennis", "active": False},
                ],
            )
        assert request.url.path == "/v4/sports/tennis_atp_us_open/odds"
        assert request.url.params["markets"] == "h2h,spreads,totals"
        return httpx.Response(200, json=[{"id": "match-1"}])

    client = TheOddsAPIClient(
        "test-key",
        "https://example.test/v4",
        httpx.Client(transport=httpx.MockTransport(handler)),
    )
    sports = client.active_tennis_sports()
    assert [sport["key"] for sport in sports] == ["tennis_atp_us_open"]
    assert client.tennis_odds(sports[0]["key"]) == [{"id": "match-1"}]


def test_tennis_odds_rejects_non_tennis_sport_key() -> None:
    client = TheOddsAPIClient("test-key")
    with pytest.raises(ValueError, match="tennis sport key"):
        client.tennis_odds("baseball_mlb")


def test_http_status_error_is_redacted_and_does_not_crash() -> None:
    """Real bug found 2026-08-04: _safe_get's redaction path reconstructed
    the exception via `type(exc)(msg)`, which works for a plain transport
    error but raises its own TypeError for httpx.HTTPStatusError (requires
    request/response keyword-only args beyond the message) -- silently
    breaking soccer score collection for every league on this provider in
    the first real daily run after the redaction fix shipped. This locks in
    that any raised error is httpx.HTTPError (what every real caller
    catches) with the API key redacted from its message, for the exact
    "401 Unauthorized" shape this failed with in production."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text=f"Unauthorized: bad key secret-key-123 in {request.url}")

    client = TheOddsAPIClient(
        "secret-key-123",
        "https://example.test/v4",
        httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(httpx.HTTPError) as excinfo:
        client.odds("MLB")

    assert "secret-key-123" not in str(excinfo.value)
    assert "[REDACTED]" in str(excinfo.value)


def test_transport_error_is_also_redacted_via_the_same_path() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"connection refused for key secret-key-123 at {request.url}")

    client = TheOddsAPIClient(
        "secret-key-123",
        "https://example.test/v4",
        httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(httpx.HTTPError) as excinfo:
        client.odds("MLB")

    assert "secret-key-123" not in str(excinfo.value)


def test_world_cup_uses_fifa_world_cup_sport_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v4/sports/soccer_fifa_world_cup/odds"
        assert request.url.params["markets"] == "h2h,spreads,totals"
        return httpx.Response(200, json=[{"id": "wc-match-1"}])

    client = TheOddsAPIClient(
        "test-key",
        "https://example.test/v4",
        httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert client.odds("WORLD_CUP") == [{"id": "wc-match-1"}]
