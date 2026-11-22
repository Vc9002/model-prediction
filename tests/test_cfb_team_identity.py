"""NCAAF structural (v2-v5) team identity: one team, one history, whatever ESPN calls it that week."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from model_prediction.features.cfb_features import CFBFeatureExtractor, CFBGameRecord
from model_prediction.features.cfb_team_identity import CFBTeamClasses, CFBTeamIdentity

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


# --------------------------------------------------------------------------- per-season FBS / FCS class
def _features(history, away: str, home: str):
    return CFBFeatureExtractor().extract_features(
        history,
        away_team=away,
        home_team=home,
        event_id="slate",
        game_start_utc=KICKOFF.isoformat(),
        season_year=2026,
        week=12,
    )


def test_fcs_team_missing_from_team_table_is_fbs_vs_fcs() -> None:
    # Maine is not in the static table; before the fix it fell back to G6 and the game read FBS-vs-FBS,
    # so the Main block_fcs gate and the FBS-vs-FCS SD slice failed open.
    history = [
        _game(1, "Maine Black Bears", "Towson Tigers", days_before=14, feed_groups=(81,)),
        _game(2, "Georgia Bulldogs", "Auburn Tigers", days_before=14, feed_groups=(80,)),
    ]
    assert _features(history, "Maine Black Bears", "Georgia Bulldogs").is_fbs_vs_fcs is True


def test_unknown_class_opponent_fails_closed(caplog) -> None:
    caplog.set_level("WARNING", logger="model_prediction.features.cfb_features")
    feat = _features([], "Nowhere Tech Owls", "Georgia Bulldogs")
    assert feat.is_fbs_vs_fcs is True
    assert "Nowhere Tech Owls" in caplog.text and "fail closed" in caplog.text


def test_this_seasons_feed_class_beats_a_stale_team_table_tier() -> None:
    # The static table lists North Dakota State as FCS; in 2026 ESPN lists its games in the FBS-only feed.
    ndsu = _game(1, "North Dakota State Bison", "Sacramento State Hornets", days_before=14, feed_groups=(80,))
    other = _game(2, "Georgia Bulldogs", "Auburn Tigers", days_before=14, feed_groups=(80,))
    assert _features([ndsu, other], "North Dakota State Bison", "Georgia Bulldogs").is_fbs_vs_fcs is False
    # PIT: the same evidence dated after kickoff is ignored, so the table's FCS tier decides.
    late = _game(3, "North Dakota State Bison", "Sacramento State Hornets", days_before=-2, feed_groups=(80,))
    assert _features([late, other], "North Dakota State Bison", "Georgia Bulldogs").is_fbs_vs_fcs is True


def test_fbs_fcs_feed_pair_labels_the_side_opposite_a_known_team() -> None:
    rows = [
        _game(1, "Maine Black Bears", "Towson Tigers", days_before=30, feed_groups=(81,)),
        _game(2, "Sacramento State Hornets", "Maine Black Bears", days_before=20, feed_groups=(80, 81)),
        _game(3, "Foo A&M Rams", "Bar State Owls", days_before=10),  # legacy row: no class evidence
    ]
    ident = CFBTeamIdentity.from_rows(rows)
    keyed = [(r, *ident.row_keys(r)) for r in rows]
    classes = CFBTeamClasses.from_keyed_rows(keyed)
    assert classes.classify(ident.key("Sacramento State Hornets"), 2026) == ("FBS", "feed")
    assert classes.classify(ident.key("Maine Black Bears"), 2026) == ("FCS", "feed")
    assert classes.classify(ident.key("Maine Black Bears"), 2027) == ("FCS", "previous_season_feed")
    assert classes.classify(ident.key("Foo A&M Rams"), 2026) == ("UNKNOWN", "no_class_evidence")


def test_fcs_opponent_missing_from_team_table_is_priced_like_a_listed_fcs_team() -> None:
    # One fallback: an FCS opponent the static table misses (Maine) must move Georgia's Elo and opponent-adjusted
    # efficiency exactly as a listed FCS opponent (Montana) does. Before, Elo priced it Mid-American and the
    # efficiency adjustment FBS Independents.
    def state(opponent: str):
        history = [
            _game(
                1,
                "Georgia Bulldogs",
                opponent,
                days_before=21,
                feed_groups=(80, 81),
                home_score=45,
                away_score=10,
            ),
            _game(2, opponent, "Towson Tigers", days_before=14, feed_groups=(81,)),
            _game(
                3,
                "Georgia Bulldogs",
                "Auburn Tigers",
                days_before=7,
                feed_groups=(80,),
                home_score=24,
                away_score=21,
            ),
        ]
        return CFBFeatureExtractor()._compute_team_state(history, "Georgia Bulldogs", KICKOFF, 2026, 12)

    maine, montana = state("Maine Black Bears"), state("Montana Grizzlies")
    assert maine.games_played == montana.games_played == 2
    # Each game's own score feeds the efficiency loop (45-10 and 24-21), not the last row the Elo loop saw.
    assert maine.raw_offense_ppg == montana.raw_offense_ppg == 34.5
    assert maine.raw_defense_ppg == montana.raw_defense_ppg == 15.5
    assert maine.elo_rating == montana.elo_rating
    assert maine.adj_offense_ppp == montana.adj_offense_ppp
    assert maine.adj_defense_ppp == montana.adj_defense_ppp


def test_slate_espn_id_beats_a_team_table_name_with_no_history() -> None:
    # The slate's "FIU Panthers" resolves in the static table to a key of its own; FIU's history is stored as
    # "Florida International Panthers" (not in the table) under ESPN id 2229. The id must win.
    rows = [
        _game(
            i, "Florida International Panthers", f"Opponent {i} Owls", days_before=60 - 7 * i, home_id="2229"
        )
        for i in range(6)
    ]
    rows += [
        _game(10 + i, "Georgia Bulldogs", f"Opponent {10 + i} Owls", days_before=60 - 7 * i) for i in range(6)
    ]
    feat = CFBFeatureExtractor().extract_features(
        rows,
        away_team="FIU Panthers",
        home_team="Georgia Bulldogs",
        event_id="slate",
        game_start_utc=KICKOFF.isoformat(),
        season_year=2026,
        week=12,
        away_team_id="2229",
    )
    assert feat.sample_games == 6


def test_efficiency_opponent_adjustment_direction() -> None:
    # Points scored against an FCS defence are shrunk and against an SEC defence credited up; points allowed to
    # an FCS offence count against the defence more, to an SEC offence less.
    def state(opponent: str, groups: tuple[int, ...]):
        history = [
            _game(
                1,
                "Georgia Bulldogs",
                opponent,
                days_before=14,
                feed_groups=groups,
                home_score=30,
                away_score=20,
            ),
            _game(2, opponent, "Towson Tigers", days_before=7, feed_groups=(81,) if 81 in groups else (80,)),
        ]
        return CFBFeatureExtractor()._compute_team_state(history, "Georgia Bulldogs", KICKOFF, 2026, 1)

    vs_fcs = state("Maine Black Bears", (80, 81))
    vs_sec = state("Alabama Crimson Tide", (80,))
    assert vs_fcs.raw_offense_ppg == vs_sec.raw_offense_ppg == 30.0
    assert vs_fcs.adj_offense_ppp < vs_sec.adj_offense_ppp
    assert vs_fcs.adj_defense_ppp > vs_sec.adj_defense_ppp
