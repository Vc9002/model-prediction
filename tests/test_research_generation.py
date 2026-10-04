from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from model_prediction.research_generation import (
    FEATURE_NAMES,
    Game,
    ResearchRow,
    build_rows,
    load_games,
    normalize_game,
    partition,
    predict_bundle,
    train_candidate,
)


def test_tennis_orientation_does_not_encode_winner():
    raw = {"event_id": "a", "event_start_utc": "2025-01-01", "winner_id": "A", "loser_id": "B"}
    first = normalize_game(raw, "TENNIS", "2026-01-01")
    second = normalize_game({**raw, "winner_id": "B", "loser_id": "A"}, "TENNIS", "2026-01-01")
    assert (first.a, first.b) == (second.a, second.b)
    assert (first.score_a, first.score_b) == (second.score_b, second.score_a)


def test_future_and_same_day_results_cannot_change_features():
    games = [Game(str(i), f"2025-01-{i + 1:02}", "A", "B", 2.0, 1.0) for i in range(20)]
    original = build_rows(games, "NBA")
    corrupted = [replace(g, score_a=1000.0) if g.date >= "2025-01-12" else g for g in games]
    revised = build_rows(corrupted, "NBA")
    a = {r.event_id: r for r in original}
    b = {r.event_id: r for r in revised}
    for event_id, row in a.items():
        if row.date <= "2025-01-13":
            assert row.features == b[event_id].features
    for row in original:
        assert datetime.fromisoformat(row.date) - datetime.fromisoformat(
            row.feature_max_event_date
        ) >= timedelta(days=2)
        assert len(row.features) == len(FEATURE_NAMES)


def test_conflicting_duplicate_removes_both_versions(tmp_path):
    rows = [
        {
            "event_id": "a",
            "event_start_utc": "2025-01-01",
            "home_team": "A",
            "away_team": "B",
            "home_score": 2,
            "away_score": 1,
        },
        {
            "event_id": "a",
            "event_start_utc": "2025-01-01",
            "home_team": "A",
            "away_team": "B",
            "home_score": 3,
            "away_score": 1,
        },
    ]
    path = tmp_path / "games.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows))
    games, audit = load_games(path, "NBA", "2026-01-01")
    assert not games
    assert audit["conflicting_event_ids"] == ["a"]


def sample_rows() -> list[ResearchRow]:
    result = []
    for day in range(120):
        date = (datetime(2025, 1, 1, tzinfo=UTC) + timedelta(days=day)).date().isoformat()
        for j in range(3):
            x = float((day + j) % 2)
            result.append(
                ResearchRow(
                    f"{day}-{j}", date, (x, float(day % 7)), int(x), x * 4 - 2, 10 + x * 4, "2024-01-01"
                )
            )
    return result


def test_partitions_never_share_calendar_dates():
    rows = sample_rows()
    groups = partition(rows)
    dates = [{rows[i].date for i in ids} for ids in groups.values()]
    for i, days in enumerate(dates):
        for other in dates[i + 1 :]:
            assert days.isdisjoint(other)
            assert max(days) < min(other)
    assert sum(map(len, groups.values())) == len(rows)


@pytest.mark.parametrize("market", ["moneyline", "total"])
def test_training_serialization_contracts_and_no_false_qualification(tmp_path, market):
    directory = tmp_path / market
    rows = sample_rows()
    result = train_candidate(rows, "NBA", market, "incumbent", directory, ("x", "day"))
    assert result["status"] == "TRAINED_RESEARCH_ONLY"
    assert result["incumbent_reproduction_gate"]["status"] == "UNVERIFIED"
    assert result["profitability"]["roi"] is None
    assert not result["promote"]
    features = np.array([[0.0, 1.0], [1.0, 1.0]])
    path = directory / "model.joblib"
    if market == "total":
        with pytest.raises(ValueError, match="exact_contract_line_required"):
            predict_bundle(path, features)
        for line in [10.0, 10.5]:
            p = predict_bundle(path, features, line=line)
            assert np.allclose(p.sum(1), 1)
            if line == 10.5:
                assert np.all(p[:, 1] == 0)
    else:
        p = predict_bundle(path, features)
        assert np.allclose(p.sum(1), 1)
        assert p[1, 1] > p[0, 1]
    path.write_bytes(path.read_bytes() + b"corruption")
    with pytest.raises(ValueError, match="artifact_hash_mismatch"):
        predict_bundle(path, features, line=10.0)


def test_output_is_never_overwritten(tmp_path):
    with pytest.raises(FileExistsError):
        train_candidate(sample_rows(), "NBA", "moneyline", "incumbent", tmp_path, ("x", "day"))


def test_signed_home_spread_requires_covering_the_opposite_margin(tmp_path):
    directory = tmp_path / "spread"
    train_candidate(sample_rows(), "NBA", "spread", "incumbent", directory, ("x", "day"))
    x = np.array([[1.0, 1.0]])
    generous = predict_bundle(directory / "model.joblib", x, line=10.5)
    difficult = predict_bundle(directory / "model.joblib", x, line=-10.5)
    assert generous[0, 2] > difficult[0, 2]
    assert generous[0, 2] > 0.9
    assert difficult[0, 2] < 0.1


def test_test_outcomes_do_not_affect_selected_model_or_calibration(tmp_path):
    rows = sample_rows()
    tests = set(partition(rows)["test"])
    altered = [replace(r, outcome=1 - r.outcome) if i in tests else r for i, r in enumerate(rows)]
    train_candidate(rows, "NBA", "moneyline", "incumbent", tmp_path / "original", ("x", "day"))
    train_candidate(altered, "NBA", "moneyline", "incumbent", tmp_path / "altered", ("x", "day"))
    original = json.loads((tmp_path / "original/recipe.json").read_text())
    revised = json.loads((tmp_path / "altered/recipe.json").read_text())
    assert original["selection"] == revised["selection"]
    assert original["temperature"] == revised["temperature"]
    x = np.array([[0.0, 1.0], [1.0, 1.0]])
    assert np.array_equal(
        predict_bundle(tmp_path / "original/model.joblib", x),
        predict_bundle(tmp_path / "altered/model.joblib", x),
    )


def test_fixed_partition_membership_survives_removing_an_entire_date():
    rows = sample_rows()
    original = partition(rows)
    remove = rows[original["train"][-1]].date
    kept = [r for r in rows if r.date != remove]
    accepted = {r.event_id for r in kept}
    fixed = {
        k: [rows[i].event_id for i in ids if rows[i].event_id in accepted] for k, ids in original.items()
    }
    result = partition(kept, split_event_ids=fixed)
    assert {k: [kept[i].event_id for i in ids] for k, ids in result.items()} == fixed
    bad = {k: list(v) for k, v in fixed.items()}
    bad["train"].append(bad["test"].pop())
    with pytest.raises(ValueError, match="partition_dates"):
        partition(kept, split_event_ids=bad)
