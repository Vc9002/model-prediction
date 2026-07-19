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
                                {"homeAway": "away", "score": "3",
                                 "team": {"id": "1", "displayName": "Aways"}},
                                {"homeAway": "home", "score": "5",
                                 "team": {"id": "2", "displayName": "Homes"}},
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


def test_bootstrap_walks_the_date_range(tmp_path) -> None:
    ingestor = Ingestor(tmp_path, client=FakeESPN(), rate_limit_seconds=0)
    summary = ingestor.bootstrap("wnba", "2026-07-01", "2026-07-03")
    assert summary["days"] == 3
