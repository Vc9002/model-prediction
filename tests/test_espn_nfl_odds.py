"""ESPN NFL consensus capture: parsing, de-vig, PIT reads, closing lookup, tamper rejection."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from model_prediction.data_sources.espn_nfl_odds import (
    capture_nfl_espn_odds,
    closing_espn_consensus,
    consensus_for_selection,
    consensus_known_at,
    devig_two_sided,
    espn_odds_path,
    parse_espn_consensus,
)

KICKOFF = "2026-11-13T01:15Z"  # Thursday night in ET (2026-11-12 20:15 ET)


def _event(event_id: str = "401873052", start: str = KICKOFF, odds: bool = True) -> dict:
    block = (
        [
            {
                "provider": {"name": "DraftKings"},
                "details": "NYG -2.5",
                "spread": -2.5,
                "overUnder": 40.5,
                "pointSpread": {
                    "home": {
                        "close": {"line": "-2.5", "odds": "-105"},
                        "open": {"line": "-1.5", "odds": "-110"},
                    },
                    "away": {
                        "close": {"line": "+2.5", "odds": "-115"},
                        "open": {"line": "+1.5", "odds": "-110"},
                    },
                },
                "total": {
                    "over": {
                        "close": {"line": "o40.5", "odds": "-118"},
                        "open": {"line": "o47.5", "odds": "-110"},
                    },
                    "under": {
                        "close": {"line": "u40.5", "odds": "-102"},
                        "open": {"line": "u47.5", "odds": "-110"},
                    },
                },
                "moneyline": {
                    "home": {"close": {"odds": "-142"}, "open": {"odds": "-125"}},
                    "away": {"close": {"odds": "+120"}, "open": {"odds": "+105"}},
                },
            }
        ]
        if odds
        else []
    )
    return {
        "id": event_id,
        "date": start,
        "competitions": [
            {
                "odds": block,
                "competitors": [
                    {"homeAway": "home", "team": {"displayName": "New York Giants"}},
                    {"homeAway": "away", "team": {"displayName": "Washington Commanders"}},
                ],
            }
        ],
    }


def test_parse_flattens_open_and_close_with_home_anchored_spread():
    rec = parse_espn_consensus(_event())
    assert rec["home_team"] == "New York Giants" and rec["away_team"] == "Washington Commanders"
    assert rec["spread_home_line"] == -2.5 and rec["spread_home_line_open"] == -1.5
    assert rec["spread_home_odds"] == -105 and rec["spread_away_odds"] == -115
    assert rec["total_line"] == 40.5 and rec["total_line_open"] == 47.5
    assert rec["total_over_odds"] == -118 and rec["total_under_odds"] == -102
    assert rec["ml_home_odds"] == -142 and rec["ml_away_odds"] == 120
    assert parse_espn_consensus(_event(odds=False)) is None


def test_devig_removes_overround_proportionally():
    p_a, p_b = devig_two_sided(-110, -110)
    assert p_a == p_b == 0.5
    p_home, p_away = devig_two_sided(-105, -115)
    assert abs(p_home + p_away - 1.0) < 1e-6
    assert p_home < 0.5 < p_away  # the -115 side is the dearer one
    assert devig_two_sided(None, -110) == (None, None)


def test_consensus_for_selection_signs_and_markets():
    rec = parse_espn_consensus(_event())
    p_home, line_home = consensus_for_selection(rec, "spread", "home")
    p_away, line_away = consensus_for_selection(rec, "spread", "away")
    assert line_home == -2.5 and line_away == 2.5
    assert abs(p_home + p_away - 1.0) < 1e-6
    p_over, t_line = consensus_for_selection(rec, "total", "over")
    p_under, _ = consensus_for_selection(rec, "total", "under")
    assert t_line == 40.5 and p_over > 0.5 > p_under  # over is the dearer side at -118
    p_ml_home, no_line = consensus_for_selection(rec, "moneyline", "home")
    assert no_line is None and p_ml_home > 0.5
    assert consensus_for_selection(None, "spread", "home") == (None, None)
    assert consensus_for_selection(rec, "spread", "over") == (None, None)


class _FakeESPN:
    def __init__(self, by_date: dict[str, list[dict]]):
        self.by_date = by_date
        self.calls: list[str] = []

    def scoreboard(self, league: str, game_date: str) -> dict:
        self.calls.append(game_date)
        return {"events": self.by_date.get(game_date, [])}


def test_capture_files_by_eastern_date_skips_started_games_and_hashes(tmp_path):
    now = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
    started = _event("1", start="2026-11-10T01:15Z")  # kicked off before `now`
    upcoming = _event("401873052")
    sunday = _event("9", start="2026-11-15T18:00Z")
    espn = _FakeESPN({"2026-11-10": [started], "2026-11-12": [upcoming], "2026-11-15": [sunday]})
    out = capture_nfl_espn_odds(tmp_path, "2026-11-10", days=7, client=espn, now=now)
    assert out["records_written"] == 2 and out["skipped_started"] == 1 and out["errors"] == []
    assert espn.calls == [f"2026-11-{d}" for d in range(10, 17)]
    # Thursday-night game at 01:15Z is filed under its Eastern date, 2026-11-12.
    path = espn_odds_path(tmp_path, "2026-11-12")
    assert path.exists() and not espn_odds_path(tmp_path, "2026-11-13").exists()
    rec = json.loads(path.read_text().splitlines()[0])
    assert rec["observed_at_utc"] == now.isoformat() and len(rec["record_hash"]) == 64


def test_closing_and_pit_reads_respect_observation_time(tmp_path):
    espn = _FakeESPN({"2026-11-12": [_event()]})
    t0 = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
    capture_nfl_espn_odds(tmp_path, "2026-11-12", days=1, client=espn, now=t0)
    # Line moves before kickoff; second capture.
    moved = _event()
    moved["competitions"][0]["odds"][0]["pointSpread"]["home"]["close"]["line"] = "-3.5"
    espn.by_date["2026-11-12"] = [moved]
    t1 = datetime(2026, 11, 12, 23, 0, tzinfo=UTC)
    capture_nfl_espn_odds(tmp_path, "2026-11-12", days=1, client=espn, now=t1)
    # A capture after kickoff must never be written, let alone read as a close.
    t2 = datetime(2026, 11, 13, 2, 0, tzinfo=UTC)
    out = capture_nfl_espn_odds(tmp_path, "2026-11-12", days=1, client=espn, now=t2)
    assert out["skipped_started"] == 1 and out["records_written"] == 0

    closing = closing_espn_consensus(tmp_path, "401873052", KICKOFF)
    assert closing["spread_home_line"] == -3.5 and closing["observed_at_utc"] == t1.isoformat()
    known_early = consensus_known_at(tmp_path, "401873052", KICKOFF, t0 + timedelta(hours=1))
    assert known_early["spread_home_line"] == -2.5
    assert consensus_known_at(tmp_path, "401873052", KICKOFF, t0 - timedelta(hours=1)) is None
    assert closing_espn_consensus(tmp_path, "nope", KICKOFF) is None


def test_tampered_record_is_not_served(tmp_path):
    espn = _FakeESPN({"2026-11-12": [_event()]})
    capture_nfl_espn_odds(
        tmp_path, "2026-11-12", days=1, client=espn, now=datetime(2026, 11, 12, 12, 0, tzinfo=UTC)
    )
    path = espn_odds_path(tmp_path, "2026-11-12")
    rec = json.loads(path.read_text().splitlines()[0])
    rec["spread_home_line"] = -9.5  # edited after the fact, hash no longer matches
    path.write_text(json.dumps(rec) + "\n")
    assert closing_espn_consensus(tmp_path, "401873052", KICKOFF) is None


@pytest.mark.parametrize("bad", ["OFF", "", None, "-50"])
def test_unusable_odds_strings_yield_no_price(bad):
    ev = _event()
    ev["competitions"][0]["odds"][0]["pointSpread"]["home"]["close"]["odds"] = bad
    rec = parse_espn_consensus(ev)
    assert rec["spread_home_odds"] is None
    assert consensus_for_selection(rec, "spread", "home") == (None, -2.5)
