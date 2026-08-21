import json

from model_prediction.ingest import Ingestor


class FakeESPN:
    def __init__(self) -> None:
        self.calls = 0

    def scoreboard(self, league: str, game_date: str):
        self.calls += 1
        return {
            "events": [
                {
                    "id": "e1",
                    "date": f"{game_date}T23:00:00Z",
                    "competitions": [
                        {
                            "status": {"type": {"completed": True}},
                            "competitors": [
                                {
                                    "homeAway": "away",
                                    "score": "3",
                                    "team": {"id": "1", "displayName": "Aways"},
                                },
                                {
                                    "homeAway": "home",
                                    "score": "5",
                                    "team": {"id": "2", "displayName": "Homes"},
                                },
                            ],
                        }
                    ],
                }
            ]
        }


def test_ingest_is_idempotent_raw_immutable_processed_deduped(tmp_path) -> None:
    fake = FakeESPN()
    ingestor = Ingestor(tmp_path, client=fake, rate_limit_seconds=0)
    first = ingestor.ingest_scores("mlb", "2026-07-10")
    assert first["fetched"] == 1 and first["new_games_appended"] == 1
    second = ingestor.ingest_scores("mlb", "2026-07-10")
    # Raw file exists, so no refetch and no duplicate append.
    assert second["fetched"] == 0 and second["cached"] == 1
    assert second["new_games_appended"] == 0
    assert fake.calls == 1
    lines = ingestor.processed_path("mlb").read_text().strip().splitlines()
    assert len(lines) == 1
    historical = ingestor.historical_path("mlb").read_text().strip().splitlines()
    assert len(historical) == 1


def test_stale_pregame_cache_for_a_past_date_is_refetched_not_trusted_forever(tmp_path) -> None:
    """Confirmed live 2026-07-26: a raw cache captured mid-day (before that
    evening's games started) froze every event at STATUS_SCHEDULED, and
    every ingest since silently treated that as the permanent record --
    data/processed/mlb/games.jsonl (and every live Elo/trend feature reading
    from it) went stale for days with no error. Immutability must apply to
    a genuinely FINAL payload, not a pregame snapshot of a date that has
    since passed."""
    fake = FakeESPN()
    ingestor = Ingestor(tmp_path, client=fake, rate_limit_seconds=0)
    past_date = "2020-01-01"  # unambiguously in the past
    raw_path = ingestor.raw_dir("mlb", past_date) / "scores_mlb.json"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(
        json.dumps(
            {
                "events": [
                    {
                        "id": "e1",
                        "date": f"{past_date}T23:00:00Z",
                        "competitions": [{"status": {"type": {"completed": False}}, "competitors": []}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    result = ingestor.ingest_scores("mlb", past_date)

    assert fake.calls == 1  # refetched despite the pre-existing raw file
    assert result["fetched"] == 1
    assert result["new_games_appended"] == 1
    # The stale pregame snapshot was overwritten with the real final payload.
    assert (
        json.loads(raw_path.read_text())["events"][0]["competitions"][0]["status"]["type"]["completed"]
        is True
    )


def test_partially_completed_past_date_cache_is_refetched_not_trusted_forever(tmp_path) -> None:
    """Confirmed live 2026-08-07: a raw cache written mid-slate froze 5
    games at STATUS_IN_PROGRESS while 10 others were already final. The old
    staleness check only flagged a cache with ZERO completed events, so this
    partially-completed snapshot was trusted forever and the in-progress
    games never reached processed/historical. Any past-date cache holding an
    unfinished event must be re-fetched."""
    fake = FakeESPN()
    ingestor = Ingestor(tmp_path, client=fake, rate_limit_seconds=0)
    past_date = "2020-01-01"
    raw_path = ingestor.raw_dir("mlb", past_date) / "scores_mlb.json"
    raw_path.parent.mkdir(parents=True, exist_ok=True)

    def competition(event_id: str, completed: bool, state: str, with_sides: bool) -> dict:
        competitors = (
            [
                {"homeAway": "away", "score": "1", "team": {"id": "1", "displayName": "Aways"}},
                {"homeAway": "home", "score": "2", "team": {"id": "2", "displayName": "Homes"}},
            ]
            if with_sides
            else []
        )
        return {
            "id": event_id,
            "date": f"{past_date}T23:00:00Z",
            "competitions": [
                {"status": {"type": {"completed": completed, "state": state}}, "competitors": competitors}
            ],
        }

    raw_path.write_text(
        json.dumps(
            {
                "events": [
                    # Must carry full away/home sides or completed_games
                    # skips it and the cache still looks zero-completed.
                    competition("e_final", True, "post", with_sides=True),
                    competition("e_live", False, "in", with_sides=True),
                ]
            }
        ),
        encoding="utf-8",
    )

    result = ingestor.ingest_scores("mlb", past_date)

    assert fake.calls == 1  # refetched despite the pre-existing raw file
    assert result["fetched"] == 1
    assert result["new_games_appended"] == 1


def test_postponed_or_canceled_events_do_not_make_a_past_date_cache_stale(tmp_path) -> None:
    """Postponed/canceled games are terminal for a past date -- ESPN will
    never play them on that date, so a cache containing only them (besides
    finals) is a genuine final record. Treating state "post" as unfinished
    would re-fetch rainout-heavy dates on every bootstrap run, churning the
    raw cache and hammering the API for nothing."""
    fake = FakeESPN()
    ingestor = Ingestor(tmp_path, client=fake, rate_limit_seconds=0)
    past_date = "2020-01-01"
    raw_path = ingestor.raw_dir("mlb", past_date) / "scores_mlb.json"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(
        json.dumps(
            {
                "events": [
                    {
                        "id": "e_final",
                        "date": f"{past_date}T19:00:00Z",
                        "competitions": [
                            {
                                "status": {"type": {"completed": True, "state": "post"}},
                                "competitors": [
                                    {
                                        "homeAway": "away",
                                        "score": "1",
                                        "team": {"id": "1", "displayName": "Aways"},
                                    },
                                    {
                                        "homeAway": "home",
                                        "score": "2",
                                        "team": {"id": "2", "displayName": "Homes"},
                                    },
                                ],
                            }
                        ],
                    },
                    {
                        "id": "e_postponed",
                        "date": f"{past_date}T23:00:00Z",
                        "competitions": [
                            {"status": {"type": {"completed": False, "state": "post"}}, "competitors": []}
                        ],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    result = ingestor.ingest_scores("mlb", past_date)

    assert fake.calls == 0  # final record -- no refetch
    assert result["cached"] == 1
    assert result["new_games_appended"] == 1  # the cached final game, not the postponed one


def _tennis_scoreboard(game_date: str) -> dict:
    return {
        "events": [
            {
                "id": "t1",
                "date": f"{game_date}T12:00:00Z",
                "name": "Test Open",
                "groupings": [
                    {
                        "competitions": [
                            {
                                "id": "c1",
                                "date": f"{game_date}T12:00:00Z",
                                "type": {"slug": "mens-singles"},
                                "status": {"type": {"completed": True}},
                                "round": {"displayName": "Final"},
                                "competitors": [
                                    {"winner": True, "athlete": {"displayName": "Player A"}},
                                    {"winner": False, "athlete": {"displayName": "Player B"}},
                                ],
                            }
                        ]
                    }
                ],
            }
        ]
    }


class FakeTennisESPN:
    def __init__(self) -> None:
        self.calls = 0

    def scoreboard(self, league: str, game_date: str):
        self.calls += 1
        return _tennis_scoreboard(game_date)


def test_final_tennis_cache_is_not_a_false_positive_stale_refetch(tmp_path) -> None:
    """Tennis events nest matches under `groupings`, not the flat
    `competitions` list `completed_games` assumes -- using that generic
    parser for the staleness check (rather than
    completed_tennis_singles_matches) made every single real, final tennis
    cache look "stale" (confirmed live: ~1748 dates falsely flagged), since
    it always sees zero completed matches for tennis regardless of true
    state. This pins the fix: a genuinely final tennis payload must not be
    refetched, checked with the SAME sport-aware parser real ingest uses."""
    fake = FakeTennisESPN()
    ingestor = Ingestor(tmp_path, client=fake, rate_limit_seconds=0)
    past_date = "2020-01-01"

    first = ingestor.ingest_scores("tennis", past_date)
    assert fake.calls == 2  # ATP + WTA
    assert first["fetched"] == 2

    second = ingestor.ingest_scores("tennis", past_date)
    assert fake.calls == 2  # not refetched -- the false-positive this guards against
    assert second["fetched"] == 0 and second["cached"] == 2


def test_genuinely_final_cache_is_never_refetched_even_for_a_past_date(tmp_path) -> None:
    """The staleness check must not defeat immutability for real historical
    data -- a cache with completed events stays untouched regardless of
    how far in the past the date is."""
    fake = FakeESPN()
    ingestor = Ingestor(tmp_path, client=fake, rate_limit_seconds=0)
    first = ingestor.ingest_scores("mlb", "2020-01-01")
    assert first["fetched"] == 1

    second = ingestor.ingest_scores("mlb", "2020-01-01")
    assert second["fetched"] == 0 and second["cached"] == 1
    assert fake.calls == 1


def test_bootstrap_walks_the_date_range(tmp_path) -> None:
    ingestor = Ingestor(tmp_path, client=FakeESPN(), rate_limit_seconds=0)
    summary = ingestor.bootstrap("wnba", "2026-07-01", "2026-07-03")
    assert summary["days"] == 3
