"""Unit tests for closing quote and CLV extraction across moneylines, spreads, and totals."""

from model_prediction.cli.settle import _closing_probability_for_moneyline_pick


def test_spread_closing_quote_resolution_home_underdog(tmp_path):
    """Test spread closing quote resolution when home team is underdog receiving points."""
    sport_dir = "nfl"
    date_str = "2026-10-11"
    snap_dir = tmp_path / "odds" / sport_dir / date_str
    snap_dir.mkdir(parents=True)
    snap_file = snap_dir / "polymarket_snapshots.jsonl"

    import json

    snap_data = {
        "event_id": "129643",
        "market_slug": "asc-nfl-det-ari-2026-10-11-neg-5pt5",
        "market_type": "spread",
        "team": "Detroit Lions",
        "line": -5.5,
        "observed_at_utc": "2026-10-11T19:00:00Z",
        "long": {"ask": 0.495, "description": "-5.50"},
        "short": {"ask": 0.510, "description": "+5.50"},
    }
    snap_file.write_text(json.dumps(snap_data) + "\n", encoding="utf-8")

    # Pick is on home team (Arizona Cardinals) receiving +5.5 points
    prob, odds = _closing_probability_for_moneyline_pick(
        data_root=tmp_path,
        sport_dir=sport_dir,
        slug="asc-nfl-det-ari-2026-10-11-neg-5pt5",
        event_start_utc="2026-10-11T20:25:00Z",
        home_team="Arizona Cardinals",
        away_team="Detroit Lions",
        selection="home",
    )

    assert prob == 0.51
    assert odds == -104


def test_spread_closing_quote_resolution_away_favorite(tmp_path):
    """Test spread closing quote resolution when away team is favorite giving points."""
    sport_dir = "nfl"
    date_str = "2026-10-11"
    snap_dir = tmp_path / "odds" / sport_dir / date_str
    snap_dir.mkdir(parents=True)
    snap_file = snap_dir / "polymarket_snapshots.jsonl"

    import json

    snap_data = {
        "event_id": "129643",
        "market_slug": "asc-nfl-det-ari-2026-10-11-neg-5pt5",
        "market_type": "spread",
        "team": "Detroit Lions",
        "line": -5.5,
        "observed_at_utc": "2026-10-11T19:00:00Z",
        "long": {"ask": 0.495, "description": "-5.50"},
        "short": {"ask": 0.510, "description": "+5.50"},
    }
    snap_file.write_text(json.dumps(snap_data) + "\n", encoding="utf-8")

    # Pick is on away team (Detroit Lions) laying -5.5 points
    prob, odds = _closing_probability_for_moneyline_pick(
        data_root=tmp_path,
        sport_dir=sport_dir,
        slug="asc-nfl-det-ari-2026-10-11-neg-5pt5",
        event_start_utc="2026-10-11T20:25:00Z",
        home_team="Arizona Cardinals",
        away_team="Detroit Lions",
        selection="away",
    )

    assert prob == 0.495
    assert odds == 102


def test_totals_closing_quote_resolution_over_under(tmp_path):
    """Test total closing quote resolution for Over and Under."""
    sport_dir = "nba"
    date_str = "2026-10-04"
    snap_dir = tmp_path / "odds" / sport_dir / date_str
    snap_dir.mkdir(parents=True)
    snap_file = snap_dir / "polymarket_snapshots.jsonl"

    import json

    snap_data = {
        "event_id": "9991",
        "market_slug": "tsc-nba-gs-lac-2026-10-04-224pt5",
        "market_type": "total",
        "line": 224.5,
        "observed_at_utc": "2026-10-04T22:00:00Z",
        "long": {"ask": 0.49, "description": "Over"},
        "short": {"ask": 0.52, "description": "Under"},
    }
    snap_file.write_text(json.dumps(snap_data) + "\n", encoding="utf-8")

    # Over pick
    prob_o, odds_o = _closing_probability_for_moneyline_pick(
        data_root=tmp_path,
        sport_dir=sport_dir,
        slug="tsc-nba-gs-lac-2026-10-04-224pt5",
        event_start_utc="2026-10-04T23:00:00Z",
        home_team="LA Clippers",
        away_team="Golden State Warriors",
        selection="over",
    )
    assert prob_o == 0.49
    assert odds_o == 104

    # Under pick
    prob_u, odds_u = _closing_probability_for_moneyline_pick(
        data_root=tmp_path,
        sport_dir=sport_dir,
        slug="tsc-nba-gs-lac-2026-10-04-224pt5",
        event_start_utc="2026-10-04T23:00:00Z",
        home_team="LA Clippers",
        away_team="Golden State Warriors",
        selection="under",
    )
    assert prob_u == 0.52
    assert odds_u == -108
