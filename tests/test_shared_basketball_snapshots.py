import json
from types import SimpleNamespace

from model_prediction.learned_forward import match_executable_quote


def test_shared_nba_archive_respects_decision_time_and_league(tmp_path):
    path = tmp_path / "odds/basketball/2026-10-20/polymarket_snapshots.jsonl"
    path.parent.mkdir(parents=True)

    def snapshot(time, ask, league="NBA"):
        return {
            "league": league,
            "market_type": "moneyline",
            "market_slug": "aec-nba-bos-det-2026-10-20",
            "observed_at_utc": time,
            "long": {"description": "Celtics", "ask": 0.53},
            "short": {"description": "Pistons", "ask": ask},
            "timestamp_valid": True,
        }

    rows = [
        snapshot("2026-10-20T16:00:00Z", 0.48),
        snapshot("2026-10-20T18:00:00Z", 0.60),
        snapshot("2026-10-20T16:30:00Z", 0.10, "WNBA"),
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows))
    candidate = SimpleNamespace(
        away_team="Boston Celtics",
        home_team="Detroit Pistons",
        selection="home",
        event_start_utc="2026-10-20T19:00:00Z",
        model_input_snapshot={"observed_at_utc": "2026-10-20T17:00:00Z"},
    )
    quote = match_executable_quote(tmp_path, "nba", "2026-10-20", candidate)
    assert quote["executable_ask"] == 0.48
    assert quote["_archive_path"] == str(path)
    assert quote["_archive_record"] == rows[0]
