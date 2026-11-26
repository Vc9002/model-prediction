"""NCAAB P1 player availability (shadow): capture, research parity on real boxes, PIT, fail-closed codes."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from model_prediction.data_sources import ncaab_boxscores as boxes
from model_prediction.domain import parse_utc
from model_prediction.features import ncaab_player_availability as avail
from model_prediction.features.base import GameRecord
from model_prediction.learned_forward import _compute_features

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures" / "ncaab_player_boxes"
# Real ESPN player boxes of the 2026-27 season (fetched 2026-11-25): teams 3246 and 2169 missed rotation
# players, and 2717 has a box that fails the research quality gate.
TEAMS = ("3246", "2169", "2717")
NOW = datetime(2026, 11, 26, tzinfo=UTC)


def _research(name: str, path: Path) -> Any:
    # scripts/ is not a package and src must not import it; the parity tests do on purpose.
    sys.path.insert(0, str(path.parent))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module  # dataclasses resolve their module through sys.modules
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(path.parent))
    return module


@pytest.fixture(scope="module")
def research_features() -> Any:
    return _research(
        "player_features_261121", REPO / "scripts" / "ncaab_research" / "player_features_261121.py"
    )


def _records() -> list[dict[str, Any]]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted((FIXTURES / "records").glob("*.json"))]


def _history(records: list[dict[str, Any]]) -> list[GameRecord]:
    return [
        GameRecord(
            r["event_id"],
            r["event_start_utc"],
            "NCAAB",
            r["away"]["team_name"],
            r["home"]["team_name"],
            int(r["away"]["score_scoreboard"] or 0),
            int(r["home"]["score_scoreboard"] or 0),
            season_year=r["season_year"],
        )
        for r in records
    ]


def _team_names(records: list[dict[str, Any]]) -> dict[str, str]:
    return {r[s]["team_id"]: r[s]["team_name"] for r in records for s in ("home", "away")}


def _write_tree(root: Path, records: list[dict[str, Any]]) -> Path:
    tree = boxes.player_box_root(root)
    for r in records:
        path = tree / r["game_date"] / f"{r['event_id']}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(r), encoding="utf-8")
    return tree


# ------------------------------------------------------------------------------------- capture / parser


def _summary() -> dict[str, Any]:
    return json.loads((FIXTURES / "summary_401924268.json").read_text(encoding="utf-8"))


def _game_from_record(record: dict[str, Any]) -> dict[str, Any]:
    game = {
        "event_id": record["event_id"],
        "event_start_utc": record["event_start_utc"],
        "status": "completed",
        "game_date": record["game_date"],
        "season_year": record["season_year"],
        "neutral": record["neutral"],
        "d1_vs_d1": record["d1_vs_d1"],
    }
    for side in ("home", "away"):
        game[f"{side}_team_id"] = record[side]["team_id"]
        game[f"{side}_team"] = record[side]["team_name"]
        game[f"{side}_score"] = record[side]["score_scoreboard"]
        game[f"{side}_conference_id"] = record[side]["conference_id"]
    return game


def test_parser_equals_the_research_backfill_on_a_real_summary() -> None:
    script = _research("backfill_ncaab_player_boxes", REPO / "scripts" / "backfill_ncaab_player_boxes.py")
    research_record = json.loads((FIXTURES / "records" / "401924268.json").read_text(encoding="utf-8"))
    game, summary = _game_from_record(research_record), _summary()
    observed = "2026-11-25T21:00:00+00:00"
    ours = boxes.parse_player_boxes(game, summary, observed)
    assert ours == script.parse_player_boxes(game, summary, observed)
    # and the research tree's own record of this game (parsed from the untrimmed summary) agrees on content
    skip = {"source_sha256", "observed_at_utc"}
    assert {k: v for k, v in ours.items() if k not in skip} == {
        k: v for k, v in research_record.items() if k not in skip
    }
    assert ours["home"]["player_box_available"] and len(ours["home"]["players"]) >= 5


class _Client:
    def __init__(self, summary: dict[str, Any], game: dict[str, Any]) -> None:
        self.calls = 0
        self._summary = summary
        self._game = game

    def scoreboard(self, *_args: Any) -> dict[str, Any]:
        return {}

    def completed_games(self, _board: Any) -> list[dict[str, Any]]:
        game = dict(self._game)
        game.pop("game_date")
        return [game]

    def summary(self, *_args: Any) -> dict[str, Any]:
        self.calls += 1
        return self._summary


def test_capture_writes_team_and_player_records_from_one_fetch(tmp_path: Path) -> None:
    record = json.loads((FIXTURES / "records" / "401924268.json").read_text(encoding="utf-8"))
    client = _Client(_summary(), _game_from_record(record))
    first = boxes.capture_completed_boxscores(tmp_path, record["game_date"], client)
    assert client.calls == 1
    assert (first["captured"], first["missing"]) == (1, [])
    assert (first["player_boxes"]["captured"], first["player_boxes"]["missing"]) == (1, [])
    stored = json.loads(
        (boxes.player_box_root(tmp_path) / record["game_date"] / "401924268.json").read_text(encoding="utf-8")
    )
    assert stored["schema"] == "ncaab-player-boxes-v1" and stored["game_date"] == record["game_date"]
    assert parse_utc(stored["observed_at_utc"]) > parse_utc(stored["event_start_utc"])
    second = boxes.capture_completed_boxscores(tmp_path, record["game_date"], client)
    assert client.calls == 1 and second["existing"] == 1 and second["player_boxes"]["existing"] == 1


def test_capture_backfills_a_missing_player_record_without_touching_the_team_record(tmp_path: Path) -> None:
    record = json.loads((FIXTURES / "records" / "401924268.json").read_text(encoding="utf-8"))
    team_path = tmp_path / "features" / "ncaab" / "boxscores" / record["game_date"] / "401924268.json"
    team_path.parent.mkdir(parents=True)
    team_path.write_text('{"first_seen": true}\n', encoding="utf-8")
    client = _Client(_summary(), _game_from_record(record))
    result = boxes.capture_completed_boxscores(tmp_path, record["game_date"], client)
    assert client.calls == 1 and result["existing"] == 1 and result["player_boxes"]["captured"] == 1
    assert team_path.read_text(encoding="utf-8") == '{"first_seen": true}\n'


def test_a_team_parse_failure_does_not_block_the_player_record(tmp_path: Path) -> None:
    record = json.loads((FIXTURES / "records" / "401924268.json").read_text(encoding="utf-8"))
    summary = _summary()
    summary["boxscore"]["teams"] = []  # team statistics gone; the player block is intact
    client = _Client(summary, _game_from_record(record))
    result = boxes.capture_completed_boxscores(tmp_path, record["game_date"], client)
    assert result["captured"] == 0 and len(result["missing"]) == 1
    assert result["player_boxes"]["captured"] == 1


# ------------------------------------------------------------------------------------- research parity


def test_coefficients_are_the_pinned_confirmation_set() -> None:
    pinned = json.loads(
        (
            REPO / "outputs/ncaab/research_261120/P1_player_availability/confirmation_coefficients.json"
        ).read_text(encoding="utf-8")
    )
    assert [avail.P1_COEF_AVAIL, avail.P1_COEF_OUT3] == pinned["coef"]


def test_box_quality_equals_research_gate_on_real_boxes(research_features: Any) -> None:
    reasons = []
    for r in _records():
        for side, other in (("home", "away"), ("away", "home")):
            ours = avail.box_quality(r[side], r[other])
            assert ours == research_features.box_quality(r[side], r[other])
            reasons.append(ours)
    assert any(reasons) and None in reasons  # the fixture exercises both outcomes


def test_availability_equals_research_definition_on_real_boxes(research_features: Any) -> None:
    records = _records()
    history = _history(records)
    names = _team_names(records)
    index = avail.PlayerBoxIndex(records, NOW, 2027)
    games = [tg for r in records for tg in research_features.team_games_from_record(r)]
    checked = valued = 0
    for team_id in TEAMS:
        team_games = sorted(
            (g for g in games if g.team_id == team_id),
            key=lambda g: (g.game_date, g.event_start_utc, g.event_id),
        )
        schedule = sorted((g.game_date, g.event_id) for g in team_games)
        for target in team_games:
            hist = [g for g in team_games if g.box_available and g.game_date < target.game_date]
            before = [(d, e) for d, e in schedule if d < target.game_date]
            expected = research_features.availability_features(hist, before)
            ours = avail.team_availability(
                names[team_id],
                parse_utc(target.event_start_utc),
                history,
                index,
            )
            checked += 1
            if expected["availability_null_reason"] is None:
                valued += 1
                assert ours.reason is None
                assert ours.avail_top7_prev1 == pytest.approx(expected["avail_top7_prev1"], abs=1e-12)
                assert ours.top7_minutes_share_out_3plus == pytest.approx(
                    expected["top7_minutes_share_out_3plus"], abs=1e-12
                )
            else:
                assert ours.reason is not None and ours.avail_top7_prev1 is None
    assert checked >= 15 and valued >= 6


def test_real_absences_move_the_adjustment_with_the_pinned_signs() -> None:
    records = _records()
    names = _team_names(records)
    index = avail.PlayerBoxIndex(records, NOW, 2027)
    history = _history(records)
    hurt = avail.team_availability(names["3246"], NOW, history, index)
    assert hurt.reason is None and hurt.avail_top7_prev1 < 0.6 and hurt.top7_minutes_share_out_3plus > 0.2
    p1 = avail.p1_margin_adjustment(names["3246"], names["2169"], NOW, history, index)
    away = p1.away
    assert p1.adjustment == pytest.approx(
        avail.P1_COEF_AVAIL * (hurt.avail_top7_prev1 - away.avail_top7_prev1)
        + avail.P1_COEF_OUT3 * (hurt.top7_minutes_share_out_3plus - away.top7_minutes_share_out_3plus)
    )
    assert p1.adjustment < 0  # the home side missing its rotation lowers the home margin
    swapped = avail.p1_margin_adjustment(names["2169"], names["3246"], NOW, history, index)
    assert swapped.adjustment == pytest.approx(-p1.adjustment)


# ------------------------------------------------------------------------------------- PIT and fail-closed


def _team_records(team_id: str) -> list[dict[str, Any]]:
    """The team's real records, each re-stamped as observed 3 h after its start (the fixture files were
    fetched on 2026-11-25, after every tip, so decisions in the past would otherwise see none of them)."""
    team = sorted(
        (r for r in _records() if team_id in (r["home"]["team_id"], r["away"]["team_id"])),
        key=lambda r: r["event_start_utc"],
    )
    for r in team:
        r["observed_at_utc"] = (parse_utc(r["event_start_utc"]) + timedelta(hours=3)).isoformat()
    return team


def _start(record: dict[str, Any]) -> datetime:
    return parse_utc(record["event_start_utc"])


def test_only_games_strictly_before_the_tip_count() -> None:
    team = _team_records("3246")
    name = _team_names(team)["3246"]
    index = avail.PlayerBoxIndex(team, NOW, 2027)
    last = team[-1]
    at_tip = avail.team_availability(name, _start(last), _history(team), index)
    after = avail.team_availability(name, _start(last) + timedelta(seconds=1), _history(team), index)
    assert at_tip.prev_event_id != last["event_id"]  # a game starting at the tip is not "before" it
    assert after.prev_event_id == last["event_id"]


def test_a_box_observed_after_the_decision_is_invisible() -> None:
    team = _team_records("3246")
    name = _team_names(team)["3246"]
    tip = _start(team[-1]) + timedelta(days=1)
    observed_late = [dict(r) for r in team]
    observed_late[-1]["observed_at_utc"] = (tip + timedelta(hours=1)).isoformat()
    decision = tip - timedelta(hours=1)
    for r in observed_late[:-1]:
        r["observed_at_utc"] = (decision - timedelta(days=1)).isoformat()
    index = avail.PlayerBoxIndex(observed_late, decision, 2027)
    result = avail.team_availability(name, tip, _history(team), index)
    assert result.prev_event_id == team[-1]["event_id"]
    assert result.reason in (avail.STALE, avail.PREV_GAME_BOX_MISSING)


def test_fewer_than_three_usable_boxes_is_insufficient_history() -> None:
    team = _team_records("3246")
    name = _team_names(team)["3246"]
    index = avail.PlayerBoxIndex(team[:2], NOW, 2027)
    result = avail.team_availability(name, _start(team[2]), _history(team[:2]), index)
    assert result.reason == avail.INSUFFICIENT_HISTORY and result.usable_boxes == 2


def test_previous_game_box_failing_the_gate_is_box_quality() -> None:
    team = [json.loads(json.dumps(r)) for r in _team_records("3246")]
    name = _team_names(team)["3246"]
    side = "home" if team[-1]["home"]["team_name"] == name else "away"
    team[-1][side]["players"][0]["points"] = (team[-1][side]["players"][0]["points"] or 0) + 7
    result = avail.team_availability(name, NOW, _history(team), avail.PlayerBoxIndex(team, NOW, 2027))
    assert result.reason == avail.BOX_QUALITY and result.detail == "points_sum_mismatch"


def test_previous_game_box_with_neither_team_name_is_box_quality() -> None:
    team = [json.loads(json.dumps(r)) for r in _team_records("3246")]
    name = _team_names(team)["3246"]
    renamed = [json.loads(json.dumps(r)) for r in team]
    side = "home" if renamed[-1]["home"]["team_name"] == name else "away"
    renamed[-1][side]["team_name"] = "Someone Else"
    result = avail.team_availability(name, NOW, _history(team), avail.PlayerBoxIndex(renamed, NOW, 2027))
    assert result.reason == avail.BOX_QUALITY and result.detail == "team_name_matches_neither_side"


def test_uncaptured_previous_game_is_stale_inside_the_capture_window_then_missing() -> None:
    team = _team_records("3246")
    name = _team_names(team)["3246"]
    prev = _start(team[-1])

    def index_for(decision: datetime) -> avail.PlayerBoxIndex:
        return avail.PlayerBoxIndex(team[:-1], decision, 2027)

    next_day = prev + timedelta(hours=20)
    stale = avail.team_availability(name, next_day + timedelta(hours=1), _history(team), index_for(next_day))
    assert stale.reason == avail.STALE and stale.prev_event_id == team[-1]["event_id"]
    later = prev + timedelta(days=3)
    missing = avail.team_availability(name, later + timedelta(hours=1), _history(team), index_for(later))
    assert missing.reason == avail.PREV_GAME_BOX_MISSING


def test_box_known_before_score_history_still_counts_as_the_previous_game() -> None:
    team = _team_records("3246")
    name = _team_names(team)["3246"]
    lagging_history = _history(team[:-1])  # score ingestion has not caught up with the last final
    result = avail.team_availability(name, NOW, lagging_history, avail.PlayerBoxIndex(team, NOW, 2027))
    assert result.reason is None and result.prev_event_id == team[-1]["event_id"]


def test_last_season_boxes_never_enter_the_rotation() -> None:
    team = [json.loads(json.dumps(r)) for r in _team_records("3246")]
    name = _team_names(team)["3246"]
    for r in team[:-3]:  # the same games a year earlier belong to the 2025-26 season
        r["event_start_utc"] = r["event_start_utc"].replace("2026-", "2025-", 1)
    index = avail.PlayerBoxIndex(team, NOW, 2027)
    assert len(index.by_team[name]) == 3
    assert avail.team_availability(name, NOW, _history(team), index).prior_games == 3


def test_index_load_reads_the_tree_and_skips_unreadable_files(tmp_path: Path) -> None:
    records = _records()
    tree = _write_tree(tmp_path, records)
    (tree / records[0]["game_date"] / "broken.json").write_text("{", encoding="utf-8")
    index = avail.PlayerBoxIndex.load(tree, NOW, 2027)
    assert len(index.by_event) == len(records)


# ------------------------------------------------------------------------------------- learned_forward gate


def _features(sport: str, names: list[str], data_root: Path) -> tuple[dict[str, float], tuple[str, ...]]:
    records = _records()
    team_names = _team_names(records)
    trend = SimpleNamespace(offensive_momentum=0.0, defensive_momentum=0.0)
    return _compute_features(
        sport,
        SimpleNamespace(raw={"market_models": {"moneyline": {"feature_names": names}}}),
        team_names["3246"],
        team_names["2169"],
        "upcoming",
        NOW.date().isoformat(),
        NOW,
        _history(records),
        SimpleNamespace(expected_home_win=lambda home, away: 0.5),
        trend,
        trend,
        data_root,
        NOW,
    )


def test_learned_forward_branch_is_inert_unless_listed(tmp_path: Path) -> None:
    _write_tree(tmp_path, _records())
    features, unavailable = _features("ncaab", [], tmp_path)
    assert not set(features) & avail.FEATURE_NAMES and not unavailable


def test_learned_forward_branch_serves_the_listed_values(tmp_path: Path) -> None:
    _write_tree(tmp_path, _records())
    features, unavailable = _features("ncaab", sorted(avail.FEATURE_NAMES), tmp_path)
    assert not unavailable
    assert features["ncaab_p1_margin_adjustment"] < 0
    assert features["ncaab_p1_margin_adjustment"] == pytest.approx(
        avail.P1_COEF_AVAIL * features["ncaab_p1_avail_top7_prev1_diff"]
        + avail.P1_COEF_OUT3 * features["ncaab_p1_top7_out3plus_share_diff"]
    )


def test_learned_forward_branch_fails_closed_to_zero_with_the_reason(tmp_path: Path) -> None:
    features, unavailable = _features("ncaab", sorted(avail.FEATURE_NAMES), tmp_path)  # empty tree
    assert all(features[name] == 0.0 for name in avail.FEATURE_NAMES)
    assert unavailable == (avail.INSUFFICIENT_HISTORY,)
    with pytest.raises(ValueError, match="NO_CALL_NCAAB_AVAILABILITY_UNSUPPORTED"):
        _features("nba", ["ncaab_p1_margin_adjustment"], tmp_path)
