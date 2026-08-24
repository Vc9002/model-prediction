"""Regression tests for fail-closed tennis derivative handling."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from model_prediction.cli.settle import _find_tennis_result, _settle_tennis_pick
from model_prediction.tennis_forward import build_tennis_slate


class _WinnerOnlyESPN:
    @staticmethod
    def scoreboard(league: str, game_date: str) -> dict:
        assert league in {"WTA", "ATP"}
        if league == "ATP":
            return {"events": []}
        return {
            "events": [
                {
                    "id": "espn-1",
                    "date": "2026-08-23T23:00:00Z",
                    "groupings": [
                        {
                            "competitions": [
                                {
                                    "id": "competition-1",
                                    "type": {"slug": "womens-singles"},
                                    "status": {"type": {"completed": True, "name": "STATUS_FINAL"}},
                                    "competitors": [
                                        {
                                            "homeAway": "away",
                                            "winner": False,
                                            "athlete": {"displayName": "Jessica Pegula"},
                                        },
                                        {
                                            "homeAway": "home",
                                            "winner": True,
                                            "athlete": {"displayName": "Coco Gauff"},
                                        },
                                    ],
                                }
                            ]
                        }
                    ],
                }
            ]
        }


class _CompletedScoreESPN(_WinnerOnlyESPN):
    def __init__(
        self,
        *,
        away_sets: list[int],
        home_sets: list[int],
        note: str | None = None,
    ) -> None:
        self.away_sets = away_sets
        self.home_sets = home_sets
        self.note = note

    def scoreboard(self, league: str, game_date: str) -> dict:
        payload = super().scoreboard(league, game_date)
        if league == "ATP":
            return payload
        competition = payload["events"][0]["groupings"][0]["competitions"][0]
        away, home = competition["competitors"]
        away["linescores"] = [{"value": value} for value in self.away_sets]
        home["linescores"] = [{"value": value} for value in self.home_sets]
        if self.note is not None:
            competition["notes"] = [{"text": self.note}]
        return payload


class _SamePlayerRematchESPN(_CompletedScoreESPN):
    def scoreboard(self, league: str, game_date: str) -> dict:
        payload = super().scoreboard(league, game_date)
        if league == "ATP":
            return payload
        competitions = payload["events"][0]["groupings"][0]["competitions"]
        target = competitions[0]
        name_only_match = json.loads(json.dumps(target))
        name_only_match["id"] = "other-competition"
        name_only_match["competitors"][0]["winner"] = True
        name_only_match["competitors"][0]["linescores"] = [{"value": 6}, {"value": 6}]
        name_only_match["competitors"][1]["winner"] = False
        name_only_match["competitors"][1]["linescores"] = [{"value": 0}, {"value": 0}]
        competitions.insert(0, name_only_match)
        return payload


class _SettlementSpy:
    def __init__(self, *, selection: str = "home", line: float | None = None) -> None:
        self.calls: list[tuple[tuple, dict]] = []
        self.selection = selection
        self.line = line

    def settle(self, *args, **kwargs) -> dict:
        self.calls.append((args, kwargs))
        away_score, home_score = args[1:3]
        if self.selection == "over":
            result = "win" if away_score + home_score > float(self.line) else "loss"
        elif self.selection == "under":
            result = "win" if away_score + home_score < float(self.line) else "loss"
        elif self.selection == "away":
            result = "win" if away_score > home_score else "loss"
        else:
            result = "win" if home_score > away_score else "loss"
        return {"result": result}


def _row(*, market_type: str, selection: str, line: float | None) -> dict:
    return {
        "pick_id": "tennis-pick-1",
        "event_id": "espn-1:competition-1",
        "event_start_utc": "2026-08-23T23:00:00Z",
        "away_team": "Jessica Pegula",
        "original_away_team": "Jessica Pegula",
        "home_team": "Coco Gauff",
        "original_home_team": "Coco Gauff",
        "market_type": market_type,
        "selection": selection,
        "line": line,
        "rationale": "market_slug=wta-pegula-gauff-2026-08-23",
    }


def test_winner_only_result_never_settles_tennis_derivative() -> None:
    ledger = _SettlementSpy()

    result = _settle_tennis_pick(
        _row(market_type="total", selection="over", line=21.5),
        ledger,
        _WinnerOnlyESPN(),
    )

    assert result == {
        "pick_id": "tennis-pick-1",
        "reason": "UNGRADEABLE_TENNIS_DERIVATIVE: actual aligned per-set game scores are required",
    }
    assert ledger.calls == []


def test_tennis_result_prefers_exact_event_identity_over_same_name_rematch() -> None:
    result = _find_tennis_result(
        _SamePlayerRematchESPN(away_sets=[2, 4], home_sets=[6, 6]),
        "2026-08-23",
        _row(market_type="total", selection="over", line=17.5),
    )

    assert result is not None
    assert result["source_result_id"] == "espn-1:competition-1"
    assert result["match_basis"] == "event_id"
    assert (result["away_games"], result["home_games"]) == (6, 12)


@pytest.mark.parametrize(("line", "expected"), [(17.5, "win"), (21.5, "loss")])
def test_full_match_total_uses_actual_games_not_winner_encoding(line: float, expected: str) -> None:
    ledger = _SettlementSpy(selection="over", line=line)

    result = _settle_tennis_pick(
        _row(market_type="total", selection="over", line=line),
        ledger,
        _CompletedScoreESPN(away_sets=[2, 4], home_sets=[6, 6]),
    )

    assert result == {"pick_id": "tennis-pick-1", "result": expected, "settled": True}
    assert ledger.calls[0][0][1:3] == (6, 12)


@pytest.mark.parametrize("note", ["Pegula retired", "Walkover", "Match abandoned"])
def test_irregular_result_never_settles_derivative(note: str) -> None:
    ledger = _SettlementSpy(selection="over", line=17.5)

    result = _settle_tennis_pick(
        _row(market_type="total", selection="over", line=17.5),
        ledger,
        _CompletedScoreESPN(away_sets=[2, 1], home_sets=[6, 2], note=note),
    )

    assert result is not None
    assert str(result["reason"]).startswith("UNGRADEABLE_TENNIS_DERIVATIVE: irregular result")
    assert ledger.calls == []


def test_misaligned_linescores_never_settle_derivative() -> None:
    ledger = _SettlementSpy(selection="over", line=17.5)

    result = _settle_tennis_pick(
        _row(market_type="total", selection="over", line=17.5),
        ledger,
        _CompletedScoreESPN(away_sets=[2], home_sets=[6, 6]),
    )

    assert result == {
        "pick_id": "tennis-pick-1",
        "reason": "UNGRADEABLE_TENNIS_DERIVATIVE: ESPN per-set linescores are missing or misaligned",
    }
    assert ledger.calls == []


def test_winner_only_result_still_settles_moneyline() -> None:
    ledger = _SettlementSpy()

    result = _settle_tennis_pick(
        _row(market_type="moneyline", selection="home", line=None),
        ledger,
        _WinnerOnlyESPN(),
    )

    assert result == {"pick_id": "tennis-pick-1", "result": "win", "settled": True}
    assert ledger.calls[0][0][1:3] == (0, 1)


def test_set_total_never_uses_full_match_game_score() -> None:
    ledger = _SettlementSpy(selection="over", line=2.5)
    row = _row(market_type="total", selection="over", line=2.5)
    row["rationale"] = "market_slug=wta-pegula-gauff-st-2pt5"

    result = _settle_tennis_pick(
        row,
        ledger,
        _CompletedScoreESPN(away_sets=[2, 4], home_sets=[6, 6]),
    )

    assert result == {
        "pick_id": "tennis-pick-1",
        "reason": (
            "UNSUPPORTED_TENNIS_SUBPERIOD_SETTLEMENT: exact set/period identity "
            "and result dimensions are unavailable"
        ),
    }
    assert ledger.calls == []


def _write_tennis_inputs(tmp_path, *, market_type: str, market_slug: str) -> None:
    history_path = tmp_path / "processed" / "tennis" / "games.jsonl"
    history_path.parent.mkdir(parents=True)
    rows = [
        {
            "event_id": f"history-{index}",
            "event_start_utc": f"2026-05-{index % 28 + 1:02d}T12:00:00Z",
            "league": "WTA",
            "winner": "Jessica Pegula" if index % 2 else "Coco Gauff",
            "loser": "Coco Gauff" if index % 2 else "Jessica Pegula",
            "surface": "Hard",
            "match_date": f"2026-05-{index % 28 + 1:02d}T12:00:00Z",
        }
        for index in range(60)
    ]
    history_path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    snapshot_path = tmp_path / "odds" / "tennis" / "2026-08-23" / "polymarket_snapshots.jsonl"
    snapshot_path.parent.mkdir(parents=True)
    snapshot_path.write_text(
        json.dumps(
            {
                "event_start_utc": "2026-08-23T23:00:00Z",
                "observed_at_utc": "2026-08-23T18:00:00Z",
                "timestamp_valid": True,
                "market_type": market_type,
                "league": "WTA",
                "market_slug": market_slug,
                "line": 2.5,
                "event_title": "Jessica Pegula vs Coco Gauff",
                "long": {"description": "Over", "ask": 0.45},
                "short": {"description": "Under", "ask": 0.57},
            }
        )
        + "\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    ("market_type", "market_slug", "reason_prefix"),
    [
        ("total", "wta-pegula-gauff-st-2pt5", "UNSUPPORTED_TENNIS_SUBPERIOD_MARKET"),
        ("spread", "wta-pegula-gauff-ss-2pt5", "UNSUPPORTED_TENNIS_SUBPERIOD_MARKET"),
        ("total", "wta-pegula-gauff-total-22pt5", "UNSUPPORTED_TENNIS_DERIVATIVE_PRICING"),
    ],
)
def test_unsupported_tennis_market_never_reaches_pricing(
    tmp_path,
    market_type: str,
    market_slug: str,
    reason_prefix: str,
) -> None:
    _write_tennis_inputs(tmp_path, market_type=market_type, market_slug=market_slug)

    result = build_tennis_slate(
        data_root=tmp_path,
        game_date="2026-08-23",
        client=_WinnerOnlyESPN(),
        observed_at=datetime(2026, 8, 23, 19, tzinfo=UTC),
    )

    assert result["priced_contracts"] == []
    assert result["priced_count"] == 0
    assert len(result["skipped"]) == 1
    skipped = result["skipped"][0]
    assert skipped["event_id"] == "espn-1:competition-1"
    assert skipped["market_slug"] == market_slug
    assert skipped["reason"].startswith(reason_prefix)
