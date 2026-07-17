import pytest

from model_prediction.validation import (
    ValidationRow,
    _grade,
    build_production_artifact,
    chronological_split,
    evaluate_reconstructed_mlb_moneyline,
    historical_pitcher_feature_audit,
    multi_market_readiness,
    run_sport_validation,
)

from test_backtester import seed_games


def test_validation_uses_three_disjoint_chronological_cohorts(tmp_path) -> None:
    store = seed_games(tmp_path, count=480)
    report = run_sport_validation(store, "test")
    split = report["split"]

    assert split["train"]["end"] < split["validation"]["start"]
    assert split["validation"]["end"] < split["locked_holdout"]["start"]
    assert report["variants"]["elo_only"]["features"] == ["elo_probability"]


def test_confidence_gap_is_an_exact_reparameterization() -> None:
    from model_prediction.validation import confidence_gap_equivalence

    audit = confidence_gap_equivalence({"status": "evaluated", "learned_threshold": 0.62})

    assert audit["equivalent_gap_threshold"] == 0.24
    assert audit["changes_selection_order"] is False
    assert audit["decision"] == "REJECT_AS_REDUNDANT_GATE"


def test_chronological_split_rejects_empty_data() -> None:
    with pytest.raises(ValueError, match="empty"):
        chronological_split([])


def test_reconstructed_price_diagnostic_fails_closed_without_file(tmp_path) -> None:
    report = evaluate_reconstructed_mlb_moneyline(seed_games(tmp_path), tmp_path / "missing.jsonl")
    assert report["status"] == "unavailable"


def test_production_artifact_pins_audited_coefficients_threshold_and_qualification() -> None:
    report = {
        "sport": "nba",
        "threshold_source": "validation only",
        "split": {
            "train": {"start": "2024-01-01", "end": "2024-06-01", "observations": 100},
            "validation": {"start": "2024-06-02", "end": "2024-08-01", "observations": 60},
            "locked_holdout": {"start": "2024-08-02", "end": "2024-10-01", "observations": 80},
        },
        "variants": {
            "elo_trend": {
                "features": ["elo_probability", "trend_gap"],
                "coefficients": {"elo_probability": 2.5, "trend_gap": 0.1},
                "intercept": -1.2,
                "primary_65": {
                    "status": "evaluated",
                    "learned_threshold": 0.62,
                    "locked_holdout": {"qualified": True, "calls": 60, "hit_rate": 0.67},
                },
            }
        },
    }

    artifact = build_production_artifact(report)

    assert artifact["model_version"] == "nba-elo-trend-lr-v3"
    assert artifact["market_models"]["moneyline"]["coefficients"] == [2.5, 0.1]
    assert artifact["market_models"]["moneyline"]["confidence_threshold"] == 0.62
    assert artifact["qualification"]["qualified"] is True
    assert len(artifact["artifact_hash"]) == 64


def test_primary_qualification_rejects_a_negative_called_month() -> None:
    outcomes = [1] * 20 + [0] * 5 + [1] * 11 + [0] * 14
    rows = [
        ValidationRow(
            "2025-12-01" if index < 25 else "2026-01-01",
            str(index),
            outcome,
            0.7,
            0.0,
            1.0,
            1.0,
            False,
            False,
        )
        for index, outcome in enumerate(outcomes)
    ]

    rows.append(ValidationRow("2026-02-01", "end", 0, 0.5, 0, 1, 1, False, False))
    result = _grade([0.7] * 50 + [0.5], rows, 0.6, qualification_eligible=True)

    assert result["hit_rate"] == 0.62
    assert result["every_called_month_positive_at_minus_110"] is False
    assert result["qualified"] is False
    assert "2026-01" in result["failures"][-1]


def test_month_with_fewer_than_ten_calls_is_reported_but_not_a_gate() -> None:
    rows = [
        ValidationRow("2025-12-01", str(index), outcome, 0.7, 0, 1, 1, False, False)
        for index, outcome in enumerate([1] * 41 + [0] * 9)
    ]
    rows.extend(
        ValidationRow("2026-01-01", f"jan-{index}", 0, 0.7, 0, 1, 1, False, False)
        for index in range(9)
    )
    rows.append(ValidationRow("2026-02-01", "end", 0, 0.5, 0, 1, 1, False, False))

    result = _grade([0.7] * 59 + [0.5], rows, 0.6, qualification_eligible=True)

    january = next(month for month in result["monthly_at_minus_110"] if month["month"] == "2026-01")
    assert january["calls"] == 9
    assert january["qualification_status"] == "insufficient_calls"
    assert result["qualified"] is True


def test_incomplete_final_month_is_provisional_even_with_ten_calls() -> None:
    rows = [
        ValidationRow("2025-12-01", str(index), outcome, 0.7, 0, 1, 1, False, False)
        for index, outcome in enumerate([1] * 41 + [0] * 9)
    ]
    rows.extend(
        ValidationRow("2026-01-15", f"jan-{index}", 0, 0.7, 0, 1, 1, False, False)
        for index in range(10)
    )

    result = _grade([0.7] * 60, rows, 0.6, qualification_eligible=True)

    january = next(month for month in result["monthly_at_minus_110"] if month["month"] == "2026-01")
    assert january["qualification_status"] == "partial_month"
    assert result["qualified"] is True


def test_historical_pitcher_audit_rejects_unversioned_retroactive_stats(tmp_path) -> None:
    import json

    path = tmp_path / "raw/mlb/2025-04-01/scores_mlb.json"
    path.parent.mkdir(parents=True)
    probable = {"playerId": "1", "statistics": [{"name": "ERA", "displayValue": "2.50"}]}
    payload = {
        "events": [
            {
                "competitions": [
                    {
                        "competitors": [
                            {"homeAway": "home", "probables": [probable]},
                            {"homeAway": "away", "probables": [probable]},
                        ]
                    }
                ]
            }
        ]
    }
    path.write_text(json.dumps(payload), encoding="utf-8")

    audit = historical_pitcher_feature_audit(seed_games(tmp_path))

    assert audit["both_starter_era_values"] == 1
    assert audit["point_in_time_valid"] is False
    assert audit["decision"] == "REJECT_HISTORICAL_PITCHER_FEATURES_LEAKAGE_RISK"


def test_basketball_multimarket_validation_requires_exact_lines(tmp_path) -> None:
    readiness = multi_market_readiness(seed_games(tmp_path), "nba")

    assert readiness["model_parameters_changed"] is False
    assert readiness["spread"] == "BLOCKED_MISSING_HISTORICAL_CONTRACT_LINES"
    assert readiness["total"] == "BLOCKED_MISSING_HISTORICAL_CONTRACT_LINES"
