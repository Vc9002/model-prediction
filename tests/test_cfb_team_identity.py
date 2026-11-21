"""NCAAF structural (v2-v5) team identity: one team, one history, whatever ESPN calls it that week."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from model_prediction.features.cfb_features import CFBFeatureExtractor, CFBGameRecord
from model_prediction.features.cfb_team_identity import CFBTeamIdentity

KICKOFF = datetime(2026, 11, 21, 20, tzinfo=UTC)


def _game(
    i: int,
    home: str,
    away: str,
    *,
    days_before: int,
    home_id: str | None = None,
    away_id: str | None = None,
    feed_groups: tuple[int, ...] | None = None,
    home_score: int = 31,
    away_score: int = 17,
) -> CFBGameRecord:
    start = KICKOFF - timedelta(days=days_before)
    return CFBGameRecord(
        event_id=f"e{i}",
        event_start_utc=start.strftime("%Y-%m-%dT%H:%MZ"),
        league="NCAAF",
        away_team=away,
        home_team=home,
        away_score=away_score,
        home_score=home_score,
        season_type="regular-season",
        season_year=2026,
        home_team_id=home_id,
        away_team_id=away_id,
        feed_groups=feed_groups,
    )


def _app_state_history() -> list[CFBGameRecord]:
    # Mid-season ESPN rename, ids missing on the daily-ingested rows: 3 + 4 games of one team.
    rows = [
        _game(i, "Appalachian State Mountaineers", f"Opponent {i} Owls", days_before=70 - 7 * i)
        for i in range(3)
    ]
    rows += [
        _game(10 + i, "App State Mountaineers", f"Opponent {10 + i} Owls", days_before=40 - 7 * i)
        for i in range(4)
    ]
    # The slate opponent: 7 games under one name.
    rows += [
        _game(20 + i, "Georgia State Panthers", f"Opponent {20 + i} Owls", days_before=70 - 7 * i)
        for i in range(7)
    ]
    return sorted(rows, key=lambda r: r.start)


def test_renamed_team_history_collapses_to_one_team_state() -> None:
    history = _app_state_history()
    ext = CFBFeatureExtractor()
    feat = ext.extract_features(
        history,
        away_team="Georgia State Panthers",
        home_team="App State Mountaineers",
        event_id="slate",
        game_start_utc=KICKOFF.isoformat(),
        season_year=2026,
        week=12,
    )
    # min(7, 7): before the fix App State saw only its 4 'App State' rows, so sample_games was 4.
    assert feat.sample_games == 7
    for name in ("App State Mountaineers", "Appalachian State Mountaineers"):
        state = ext._compute_team_state(history, name, KICKOFF, 2026, 12)
        assert state.games_played == 7


def test_accent_and_apostrophe_variants_share_a_key() -> None:
    rows = [
        _game(1, "San José State Spartans", "Hawai'i Rainbow Warriors", days_before=20),
        _game(2, "San Jose State Spartans", "Hawaii Rainbow Warriors", days_before=10),
    ]
    ident = CFBTeamIdentity.from_rows(rows)
    assert ident.row_keys(rows[0]) == ident.row_keys(rows[1])


def test_unresolvable_names_merge_on_espn_id_and_on_normalised_name() -> None:
    rows = [
        _game(1, "Stephen F. Austin Lumberjacks", "X Owls", days_before=30, home_id="2617", away_id="9"),
        _game(2, "Stephen F Austin Lumberjacks", "X Owls", days_before=20, home_id="2617", away_id="9"),
        # An id-less daily row under a punctuation variant still joins the id-bearing history.
        _game(3, "Stephen F. Austin  Lumberjacks", "X Owls", days_before=10),
    ]
    ident = CFBTeamIdentity.from_rows(rows)
    keys = {ident.row_keys(r)[0] for r in rows}
    assert len(keys) == 1
    # A slate display name the history never used resolves through the event's ESPN team id.
    assert ident.key("SFA Lumberjacks", "2617") in keys
    assert ident.key("SFA Lumberjacks") not in keys


def test_identity_ignores_rows_after_the_decision_time() -> None:
    history = _app_state_history()
    # A post-kickoff row under the old name must not reach the state (PIT).
    late = _game(99, "Appalachian State Mountaineers", "Late Owls", days_before=-1)
    state = CFBFeatureExtractor()._compute_team_state(
        [*history, late], "App State Mountaineers", KICKOFF, 2026, 12
    )
    assert state.games_played == 7
