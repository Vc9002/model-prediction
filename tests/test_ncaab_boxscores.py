"""NCAAB box-score capture only admits first-seen terminal evidence."""

import json

import pytest

from model_prediction.data_sources.ncaab_boxscores import (
    capture_completed_boxscores,
    parse_completed_boxscore,
)


def _game():
    return {
        "event_id": "123",
        "event_start_utc": "2025-11-01T20:00:00Z",
        "status": "completed",
        "home_team_id": "1",
        "away_team_id": "2",
        "home_team": "Home",
        "away_team": "Away",
        "home_score": 75,
        "away_score": 70,
    }


def _summary():
    def team(team_id):
        return {
            "team": {"id": team_id},
            "statistics": [
                {"name": "fieldGoalsMade-fieldGoalsAttempted", "displayValue": "25-60"},
                {"name": "freeThrowsMade-freeThrowsAttempted", "displayValue": "15-20"},
                {"name": "offensiveRebounds", "displayValue": "10"},
                {"name": "turnovers", "displayValue": "12"},
            ],
        }

    return {
        "header": {"competitions": [{"id": "123", "status": {"type": {"completed": True}}}]},
        "boxscore": {"teams": [team("1"), team("2")]},
    }


def test_boxscore_requires_matching_terminal_event_and_complete_stats():
    game, summary = _game(), _summary()
    record = parse_completed_boxscore(game, summary, "2025-11-02T01:00:00Z")
    assert record["home"]["field_goal_attempts"] == 60
    assert len(record["source_sha256"]) == 64
    summary["header"]["competitions"][0]["status"]["type"]["completed"] = False
    with pytest.raises(ValueError, match="terminal"):
        parse_completed_boxscore(game, summary, "2025-11-02T01:00:00Z")
    summary = _summary()
    summary["boxscore"]["teams"][0]["statistics"].pop()
    with pytest.raises(ValueError, match="components_missing"):
        parse_completed_boxscore(game, summary, "2025-11-02T01:00:00Z")


def test_capture_is_idempotent_and_ignores_unplayed_event(tmp_path):
    class Client:
        calls = 0

        def scoreboard(self, *_args):
            return {
                "events": [
                    {
                        "id": "123",
                        "date": "2025-11-01T20:00:00Z",
                        "competitions": [
                            {
                                "status": {"type": {"completed": True}},
                                "competitors": [
                                    {
                                        "homeAway": "home",
                                        "team": {"id": "1", "displayName": "Home"},
                                        "score": "75",
                                    },
                                    {
                                        "homeAway": "away",
                                        "team": {"id": "2", "displayName": "Away"},
                                        "score": "70",
                                    },
                                ],
                            }
                        ],
                    },
                    {
                        "id": "future",
                        "date": "2025-11-01T22:00:00Z",
                        "competitions": [{"status": {"type": {"completed": False}}, "competitors": []}],
                    },
                ]
            }

        def completed_games(self, board):
            from model_prediction.data_sources.espn import ESPNClient

            return ESPNClient.completed_games(board)

        def summary(self, *_args):
            self.calls += 1
            return _summary()

    client = Client()
    first = capture_completed_boxscores(tmp_path, "2025-11-01", client)
    second = capture_completed_boxscores(tmp_path, "2025-11-01", client)
    assert (first["completed"], first["captured"], first["missing"]) == (1, 1, [])
    assert (second["existing"], client.calls) == (1, 1)
    record = json.loads((tmp_path / "features/ncaab/boxscores/2025-11-01/123.json").read_text())
    assert record["event_id"] == "123"


# ------------------------------------------------------------------------------------- hardening (review 2026-11-25)


def _event(event_id: str, home_conf: str | None = "9", away_conf: str | None = "5", neutral: bool = True):
    return {
        "id": event_id,
        "date": "2025-11-01T20:00:00Z",
        "competitions": [
            {
                "status": {"type": {"completed": True, "state": "post"}},
                "neutralSite": neutral,
                "competitors": [
                    {
                        "homeAway": "home",
                        "team": {"id": "1", "displayName": "Home", "conferenceId": home_conf},
                        "score": "75",
                    },
                    {
                        "homeAway": "away",
                        "team": {"id": "2", "displayName": "Away", "conferenceId": away_conf},
                        "score": "70",
                    },
                ],
            }
        ],
    }


class _Games:
    """Scoreboard of several finals; ``failing`` maps an event id to the error its summary raises."""

    def __init__(self, ids, failing=None, **event_kwargs):
        self.ids, self.failing, self.calls = list(ids), dict(failing or {}), []
        self.event_kwargs = event_kwargs

    def scoreboard(self, *_args):
        return {"events": [_event(i, **self.event_kwargs) for i in self.ids]}

    def completed_games(self, board):
        from model_prediction.data_sources.espn import ESPNClient

        return ESPNClient.completed_games(board)

    def summary(self, _league, event_id):
        self.calls.append(event_id)
        if event_id in self.failing:
            raise self.failing[event_id]
        summary = _summary()
        summary["header"]["competitions"][0]["id"] = event_id
        return summary


def _player_path(root, event_id, day="2025-11-01"):
    return root / "features" / "ncaab" / "player_boxes" / day / f"{event_id}.json"


def _team_path(root, event_id, day="2025-11-01"):
    return root / "features" / "ncaab" / "boxscores" / day / f"{event_id}.json"


@pytest.mark.parametrize("crash", ["mid_write", "fsync"])
def test_a_crash_mid_write_leaves_no_final_file_and_is_recaptured(tmp_path, monkeypatch, crash):
    import os

    from model_prediction.data_sources import ncaab_boxscores as module

    def partial_dump(obj, handle, **_kwargs):
        handle.write('{"truncated": ')
        raise OSError("killed mid-write")

    def failing_fsync(_fd):
        raise OSError("killed before the record was durable")

    with monkeypatch.context() as patch:
        if crash == "mid_write":
            patch.setattr(module.json, "dump", partial_dump)
        else:
            patch.setattr(os, "fsync", failing_fsync)
        result = capture_completed_boxscores(tmp_path, "2025-11-01", _Games(["10"]))
    assert result["missing"] and result["player_boxes"]["missing"]
    for final in (_team_path(tmp_path, "10"), _player_path(tmp_path, "10")):
        assert not final.exists()
        assert not any(final.parent.iterdir())  # and no temp file is left behind
    # the next run captures it: a crash never produced first-seen evidence
    again = capture_completed_boxscores(tmp_path, "2025-11-01", _Games(["10"]))
    assert (again["captured"], again["player_boxes"]["captured"]) == (1, 1)
    assert json.loads(_player_path(tmp_path, "10").read_text())["event_id"] == "10"


def test_first_seen_write_never_replaces_an_existing_record(tmp_path):
    from model_prediction.data_sources.ncaab_boxscores import _write_first_seen

    path = tmp_path / "d" / "1.json"
    assert _write_first_seen(path, {"a": 1}) is True
    assert _write_first_seen(path, {"a": 2}) is False
    assert json.loads(path.read_text()) == {"a": 1} and [p.name for p in path.parent.iterdir()] == ["1.json"]


def test_first_seen_write_falls_back_to_rename_without_hard_links(tmp_path, monkeypatch):
    import os

    from model_prediction.data_sources.ncaab_boxscores import _write_first_seen

    def no_links(*_args):
        raise OSError("hard links unsupported")

    monkeypatch.setattr(os, "link", no_links)
    path = tmp_path / "d" / "1.json"
    assert _write_first_seen(path, {"a": 1}) is True
    assert _write_first_seen(path, {"a": 2}) is False
    assert json.loads(path.read_text()) == {"a": 1} and [p.name for p in path.parent.iterdir()] == ["1.json"]


def test_a_rename_that_loses_a_race_reports_not_written(tmp_path, monkeypatch):
    import os

    from model_prediction.data_sources.ncaab_boxscores import _write_first_seen

    def no_links(*_args):
        raise OSError("hard links unsupported")

    def lost_race(*_args):
        raise FileExistsError("another writer got there first")

    monkeypatch.setattr(os, "link", no_links)
    monkeypatch.setattr(os, "rename", lost_race)
    assert _write_first_seen(tmp_path / "d" / "1.json", {"a": 1}) is False


def test_a_locked_temp_file_does_not_fail_a_successful_first_seen_write(tmp_path, monkeypatch):
    from pathlib import Path

    from model_prediction.data_sources.ncaab_boxscores import _write_first_seen

    real_unlink = Path.unlink

    def locked(self, *args, **kwargs):
        if self.name.endswith(".tmp"):
            raise PermissionError("antivirus holds the temp file")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", locked)
    path = tmp_path / "d" / "1.json"
    assert _write_first_seen(path, {"a": 1}) is True
    assert json.loads(path.read_text()) == {"a": 1}


def test_a_corrupt_existing_record_is_quarantined_and_recaptured(tmp_path):
    for path in (_team_path(tmp_path, "10"), _player_path(tmp_path, "10")):
        path.parent.mkdir(parents=True)
        path.write_text('{"event_id": "10", "home": {', encoding="utf-8")  # truncated by a kill
    client = _Games(["10"])
    result = capture_completed_boxscores(tmp_path, "2025-11-01", client)
    assert client.calls == ["10"]
    assert (result["captured"], result["existing"]) == (1, 0)
    assert (result["player_boxes"]["captured"], result["player_boxes"]["existing"]) == (1, 0)
    for path in (_team_path(tmp_path, "10"), _player_path(tmp_path, "10")):
        assert json.loads(path.read_text())["event_id"] == "10"
        assert len(list(path.parent.glob("*.corrupt-*"))) == 1  # kept for the audit, never loaded as *.json
    again = capture_completed_boxscores(tmp_path, "2025-11-01", client)
    assert client.calls == ["10"] and again["existing"] == 1 and again["player_boxes"]["existing"] == 1


def test_one_failed_summary_fetch_does_not_fail_the_others(tmp_path):
    import httpx

    request = httpx.Request("GET", "https://example.test/summary")
    rate_limited = httpx.HTTPStatusError(
        "429", request=request, response=httpx.Response(429, request=request)
    )
    broken = httpx.ConnectError("reset", request=request)
    client = _Games(["10", "11", "12"], failing={"10": rate_limited, "12": broken})
    # the team record of the failing game exists, so only the player record triggers its fetch
    done = _team_path(tmp_path, "10")
    done.parent.mkdir(parents=True)
    done.write_text(json.dumps({"event_id": "10"}), encoding="utf-8")
    result = capture_completed_boxscores(tmp_path, "2025-11-01", client)
    assert result["completed"] == 3
    assert (result["captured"], result["existing"]) == (1, 1)  # 11 captured, 10 already there
    assert [m["event_id"] for m in result["missing"]] == ["12"]
    assert sorted(m["event_id"] for m in result["player_boxes"]["missing"]) == ["10", "12"]
    assert _player_path(tmp_path, "11").exists() and not _player_path(tmp_path, "10").exists()


def test_a_permanent_player_parse_failure_stops_refetching_after_two_attempts(tmp_path):
    team = _team_path(tmp_path, "10")
    team.parent.mkdir(parents=True)
    team.write_text(json.dumps({"event_id": "10"}), encoding="utf-8")

    class Broken(_Games):
        def summary(self, league, event_id):
            summary = super().summary(league, event_id)
            summary["header"]["competitions"][0]["id"] = "not-this-event"  # parser refuses it, every time
            return summary

    client = Broken(["10"])
    runs = [capture_completed_boxscores(tmp_path, "2025-11-01", client) for _ in range(4)]
    assert client.calls == ["10", "10"]  # two attempts, then the marker stops the refetching
    reasons = [r["player_boxes"]["missing"][0]["reason"] for r in runs]
    assert reasons[0] == reasons[1] == "summary_event_id_mismatch"
    assert reasons[2] == reasons[3] == "player_parse_failed_2_attempts"
    assert not _player_path(tmp_path, "10").exists()
    assert all(r["missing"] == [] for r in runs)  # the team record is complete: daily status stays "ok"


def test_a_parse_failure_marker_from_an_older_parser_does_not_block_a_retry(tmp_path):
    from model_prediction.data_sources import ncaab_boxscores as module

    team = _team_path(tmp_path, "10")
    team.parent.mkdir(parents=True)
    team.write_text(json.dumps({"event_id": "10"}), encoding="utf-8")
    marker = module._parse_failure_marker(_player_path(tmp_path, "10"))
    marker.parent.mkdir(parents=True)
    marker.write_text(json.dumps({"attempts": 2, "reason": "old bug", "parser": "retired-parser"}))
    client = _Games(["10"])
    result = capture_completed_boxscores(tmp_path, "2025-11-01", client)
    assert client.calls == ["10"] and result["player_boxes"]["captured"] == 1
    assert not marker.exists()
    # a marker the current parser wrote still stops the refetching
    marker.write_text(json.dumps({"attempts": 2, "parser": module.PLAYER_PARSER_VERSION}))
    _player_path(tmp_path, "10").unlink()
    capture_completed_boxscores(tmp_path, "2025-11-01", client)
    assert client.calls == ["10"]


def test_conference_ids_and_neutral_site_come_from_the_scoreboard(tmp_path):
    capture_completed_boxscores(tmp_path, "2025-11-01", _Games(["10"], neutral=True))
    record = json.loads(_player_path(tmp_path, "10").read_text())
    assert (record["home"]["conference_id"], record["away"]["conference_id"]) == ("9", "5")
    assert record["neutral"] is True and record["d1_vs_d1"] is True
    capture_completed_boxscores(tmp_path, "2025-11-01", _Games(["11"], away_conf=None, neutral=False))
    other = json.loads(_player_path(tmp_path, "11").read_text())
    assert other["away"]["conference_id"] is None and other["neutral"] is False and other["d1_vs_d1"] is False


def test_backfill_walks_every_date_and_is_idempotent(tmp_path):
    from model_prediction.data_sources.ncaab_boxscores import backfill_player_boxes

    class Days(_Games):
        def scoreboard(self, _league, day):
            board = super().scoreboard()
            for event in board["events"]:
                event["id"] = f"{day}-{event['id']}"
            return board

    client = Days(["1"])
    out = backfill_player_boxes(tmp_path, "2025-11-01", "2025-11-03", client)
    assert [d["date"] for d in out["days"]] == ["2025-11-01", "2025-11-02", "2025-11-03"]
    assert out["captured"] == 3 and out["player_boxes_captured"] == 3 and out["errors"] == []
    again = backfill_player_boxes(tmp_path, "2025-11-01", "2025-11-03", client)
    assert again["captured"] == 0 and again["player_boxes_captured"] == 0 and len(client.calls) == 3
    with pytest.raises(ValueError, match="since_after_until"):
        backfill_player_boxes(tmp_path, "2025-11-04", "2025-11-03", client)


def test_cli_backfill_command_writes_under_the_given_data_root(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from model_prediction.cli.commands import cmd_ncaab_player_box_backfill
    from model_prediction.cli.parser import parser
    from model_prediction.data_sources import ncaab_boxscores as module

    games = _Games(["1"])
    games.client = SimpleNamespace(close=lambda: None)
    monkeypatch.setattr(module, "ESPNClient", lambda: games)
    args = parser().parse_args(
        ["ncaab-player-box-backfill", "--since", "2025-11-01", "--until", "2025-11-02"]
    )
    out = cmd_ncaab_player_box_backfill(args, None, None, None, None, None, tmp_path)
    assert (out["since"], out["until"], out["captured"], out["errors"]) == ("2025-11-01", "2025-11-02", 2, [])
    assert _player_path(tmp_path, "1", "2025-11-02").exists()
    assert parser().parse_args(["ncaab-player-box-backfill", "--since", "2025-11-01"]).until is None
