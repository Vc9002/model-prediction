"""Regression tests for the official WNBA injury-report PDF parser.

2026-08-26 bug: every morning/midday report on 08-26 failed to parse with
"official WNBA injury report row is missing game/team context" while the
afternoon/evening reports parsed fine. Root cause: the game date is
printed on the first row of a game block, which can be a team-level
"NOT YET SUBMITTED" row (no player, so not a player line) -- the entry
pass only propagated date/time/matchup/team context through *player*
lines, so the first real player row of the day had no date and the whole
report failed closed. Fixtures are real reports downloaded 2026-08-26:
the morning variant reproduces the failure, the afternoon variant is the
control that always parsed.
"""

from pathlib import Path

from model_prediction.data_sources.wnba_injuries import parse_report_pdf

FIXTURES = Path(__file__).parent / "fixtures" / "wnba"


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def test_morning_report_with_not_yet_submitted_team_parses() -> None:
    parsed = parse_report_pdf(_fixture("report-morning-not-yet-submitted.pdf"))

    # The report's own publication timestamp, parsed from the header.
    assert parsed.report_at_utc == "2026-08-26T16:30:00+00:00"
    # Connecticut Sun submitted nothing yet; Toronto Tempo's players parse.
    assert parsed.team_report_status["Connecticut Sun"] == "not_yet_submitted"
    assert parsed.team_report_status["Toronto Tempo"] == "submitted"
    assert "Connecticut Sun" in parsed.teams_listed
    assert "Toronto Tempo" in parsed.teams_listed
    # Every entry must carry the game date that only appeared on the
    # team-level row -- this was the value the old parser never propagated.
    assert parsed.entries
    assert all(entry.game_date == "2026-08-26" for entry in parsed.entries)
    assert {entry.team for entry in parsed.entries} <= set(parsed.teams_listed)
    # The submitted team's players parse into entries; the not-yet-submitted
    # team correctly has none.
    assert any(entry.team == "Toronto Tempo" for entry in parsed.entries)
    assert not any(entry.team == "Connecticut Sun" for entry in parsed.entries)


def test_afternoon_report_still_parses_unchanged() -> None:
    parsed = parse_report_pdf(_fixture("report-afternoon-submitted.pdf"))

    assert parsed.report_at_utc == "2026-08-26T16:45:00+00:00"
    assert len(parsed.entries) == 9
    assert all(entry.game_date == "2026-08-26" for entry in parsed.entries)


def test_morning_fixture_has_no_zero_player_rows() -> None:
    parsed = parse_report_pdf(_fixture("report-morning-not-yet-submitted.pdf"))
    for entry in parsed.entries:
        assert entry.player_name
        assert entry.current_status in {"Available", "Probable", "Questionable", "Doubtful", "Out"}
