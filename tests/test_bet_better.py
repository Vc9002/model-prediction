"""Tests for the Bet Better open model-feed client and its capture path.

Envelope/pick shapes mirror the provider's live responses (verified
2026-08-26 with real unauthenticated calls). No test here touches the
network.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from model_prediction.data_sources.bet_better import (
    _BET_BETTER_SPORT,
    BET_BETTER_ATTRIBUTION,
    BET_BETTER_FEEDS,
    BET_BETTER_LICENCE,
    BetBetterClient,
    collect_bet_better_models,
)

OBSERVED = datetime(2026, 8, 26, 12, 0, 0, tzinfo=UTC)


def make_pick(
    *,
    game: str = "Milwaukee Brewers @ New York Mets",
    game_time: str = "2026-08-27T23:10:00.0000000Z",
    market: str = "Moneyline",
    selection: str = "New York Mets",
    line=None,
    prob: float = 54.9,
) -> dict:
    return {
        "game": game,
        "gameTimeUtc": game_time,
        "market": market,
        "selection": selection,
        "line": line,
        "modelProbabilityPct": prob,
        "fairOdds": 1.95,
        "confidence": "LEAN",
        "verdict": "A model estimate.",
    }


def make_envelope(picks: list[dict] | None, *, updated: str = "2026-08-26T12:00:00Z") -> dict:
    return {
        "site": "Bet Better",
        "page": "https://betbetter.world/mlb/picks",
        "sport": "MLB",
        "type": "picks",
        "updatedUtc": updated,
        "licence": "CC BY 4.0 — free to use with attribution to Bet Better (https://betbetter.world)",
        "attribution": "Bet Better — https://betbetter.world",
        "docs": "https://betbetter.world/api/",
        "disclaimer": "Model estimates for research. Not a guarantee. 18+.",
        "count": len(picks),
        "picks": picks,
    }


class StubBetBetterClient:
    """Records every feed path and returns canned envelopes per path."""

    def __init__(self, envelopes_by_path: dict[str, dict] | None = None) -> None:
        self.envelopes_by_path = envelopes_by_path or {}
        self.calls: list[str] = []

    def picks(self, feed_path: str) -> dict:
        self.calls.append(feed_path)
        return self.envelopes_by_path[feed_path]


class ExplodingClient:
    def picks(self, feed_path: str) -> dict:
        raise httpx.HTTPError(f"boom for {feed_path}")


def _snapshot(report: dict, label: str) -> dict:
    path = Path(report[label]["snapshot_path"])
    return json.loads(path.read_text(encoding="utf-8"))


# --- contract tables ------------------------------------------------------


def test_feed_table_is_complete_and_consistent() -> None:
    # Every feed label has exactly one sport bucket; no duplicate paths.
    assert set(BET_BETTER_FEEDS) == set(_BET_BETTER_SPORT)
    paths = list(BET_BETTER_FEEDS.values())
    assert len(paths) == len(set(paths))
    for path in paths:
        assert path.startswith("/") and "picks" in path


# --- client contract ------------------------------------------------------


def test_client_requests_canonical_url_with_format_param() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json=make_envelope([]))

    client = BetBetterClient(client=httpx.Client(transport=httpx.MockTransport(handler)))
    envelope = client.picks("/mlb/picks")
    assert envelope["sport"] == "MLB"
    assert captured["url"] == "https://betbetter.world/mlb/picks?format=json"


def test_client_transport_error_is_an_httpx_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = BetBetterClient(client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(httpx.HTTPError):
        client.picks("/mlb/picks")


# --- capture path ---------------------------------------------------------


def test_collect_writes_snapshots_with_attribution_and_pick_entries(tmp_path) -> None:
    picks = [
        make_pick(market="Moneyline", line=None),
        make_pick(market="Spread", selection="New York Mets", line=-1.5, prob=62.0),
    ]
    client = StubBetBetterClient({"/mlb/picks": make_envelope(picks)})
    report = collect_bet_better_models(
        tmp_path,
        feeds={"MLB": "/mlb/picks"},
        client=client,
        request_delay=0,
        observed_at=OBSERVED,
    )

    assert client.calls == ["/mlb/picks"]
    assert report["MLB"]["status"] == "ok"
    assert report["MLB"]["picks_returned"] == 2
    assert report["total_picks"] == 2
    assert report["licence"] == BET_BETTER_LICENCE
    assert report["attribution"] == BET_BETTER_ATTRIBUTION

    snap = _snapshot(report, "MLB")
    assert snap["source"] == "bet_better"
    assert snap["sport"] == "mlb"
    # two picks + one envelope entry
    assert snap["entry_count"] == 3
    entries = snap["entries"]
    envelope_entry = next(e for e in entries if e["source_entity_id"] == "MLB:envelope")
    assert envelope_entry["available"] is True
    # the CC BY attribution travels with the raw capture, without the picks
    assert "picks" not in envelope_entry["payload"]
    assert "licence" in envelope_entry["payload"]
    assert "attribution" in envelope_entry["payload"]

    pick_entries = [e for e in entries if e["source_entity_id"] != "MLB:envelope"]
    assert len(pick_entries) == 2
    for entry, pick in zip(pick_entries, picks):
        # provider's own identifying tuple, verbatim -- never a minted id
        expected_id = f"{pick['gameTimeUtc']}|{pick['market']}|{pick['selection']}|{pick['line']}"
        assert entry["source_entity_id"] == expected_id
        assert entry["effective_at_utc"] == pick["gameTimeUtc"]
        assert entry["observed_at_utc"] == OBSERVED.isoformat()
        assert entry["payload"] == pick


def test_empty_feed_is_captured_as_provider_empty_not_fetch_failure(tmp_path) -> None:
    client = StubBetBetterClient({"/nba/picks": make_envelope([])})
    report = collect_bet_better_models(
        tmp_path,
        feeds={"NBA": "/nba/picks"},
        client=client,
        request_delay=0,
        observed_at=OBSERVED,
    )

    assert report["NBA"]["status"] == "ok"
    assert report["NBA"]["picks_returned"] == 0
    snap = _snapshot(report, "NBA")
    assert snap["entry_count"] == 1
    entry = snap["entries"][0]
    assert entry["available"] is False
    assert "0 picks" in entry["missing_reason"]
    # the envelope's own updatedUtc proves the response was live
    assert entry["payload"]["updatedUtc"] == "2026-08-26T12:00:00Z"


def test_per_league_failure_is_fail_soft_and_other_leagues_still_capture(tmp_path) -> None:
    ok_client = StubBetBetterClient({"/mlb/picks": make_envelope([make_pick()])})

    class PartialClient:
        def picks(self, feed_path: str) -> dict:
            if feed_path == "/nfl/picks":
                raise httpx.HTTPError("timeout")
            return ok_client.picks(feed_path)

    report = collect_bet_better_models(
        tmp_path,
        feeds={"MLB": "/mlb/picks", "NFL": "/nfl/picks"},
        client=PartialClient(),
        request_delay=0,
        observed_at=OBSERVED,
    )

    assert report["NFL"]["status"] == "error"
    assert "timeout" in report["NFL"]["error"]
    assert report["MLB"]["status"] == "ok"
    assert report["MLB"]["picks_returned"] == 1
    assert report["total_picks"] == 1


def test_malformed_envelope_is_fail_soft(tmp_path) -> None:
    client = StubBetBetterClient({"/mlb/picks": {"sport": "MLB"}})  # no picks list
    report = collect_bet_better_models(
        tmp_path,
        feeds={"MLB": "/mlb/picks"},
        client=client,
        request_delay=0,
        observed_at=OBSERVED,
    )
    assert report["MLB"]["status"] == "error"
    assert "no picks list" in report["MLB"]["error"]


def test_collect_sorts_and_reports_all_feeds(tmp_path) -> None:
    client = StubBetBetterClient({path: make_envelope([]) for path in BET_BETTER_FEEDS.values()})
    report = collect_bet_better_models(
        tmp_path,
        feeds=BET_BETTER_FEEDS,
        client=client,
        request_delay=0,
        observed_at=OBSERVED,
    )
    assert report["feeds"] == len(BET_BETTER_FEEDS)
    assert report["total_picks"] == 0
    assert all(report[label]["status"] == "ok" for label in BET_BETTER_FEEDS)
